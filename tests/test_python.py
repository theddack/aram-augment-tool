"""파이썬 쪽 테스트: python -m unittest discover -s tests

패치 노트 대조, 자동 추정·위키 대조, 근거, 기록 이름 가리기, 아이템 세트 저장 보호, 주소 제한.
네트워크·롤 클라이언트·GUI 없이 돈다 (pywebview 가 없으면 가짜 모듈로 대신한다).
"""
import copy
import json
import os
import pathlib
import sys
from pathlib import Path
import tempfile
import time
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    import webview  # noqa: F401
except Exception:  # CI 처럼 GUI 라이브러리가 없으면 desktop.py 를 불러올 수 있게만
    sys.modules["webview"] = types.ModuleType("webview")

import build_data as B  # noqa: E402
import desktop  # noqa: E402
import lcu  # noqa: E402
from patch_check import check_augment, check_champion, presence, split_line  # noqa: E402

DATA = json.loads((ROOT / "web" / "data.json").read_text(encoding="utf-8"))
CHAMP = {c["id"]: c for c in DATA["champions"]}


class PatchCheck(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_line("W - 용기 재사용 대기시간: 22~12초 ⇒ 18~12초"), ("W", "용기 재사용 대기시간", "22~12초", "18~12초"))
        self.assertEqual(split_line("기본 지속 효과 - 인내심: 1% ⇒ 2%")[0], "P")
        self.assertIsNone(split_line("삭제 마나 흡수: 삭제"))

    def test_mode_override_applied_when_wiki_is_old(self):
        sw = copy.deepcopy(CHAMP["Swain"])
        sw["aram"]["dmg_taken"] = 1.05
        r = check_champion(sw, ["받는 피해량: 105% ⇒ 100%"])[0]
        self.assertEqual(r["status"], "applied")
        self.assertAlmostEqual(sw["aram"]["dmg_taken"], 1.0)

    def test_mode_override_differ_is_not_applied(self):
        sw = copy.deepcopy(CHAMP["Swain"])
        r = check_champion(sw, ["가하는 피해량: 90% ⇒ 95%"])[0]
        self.assertEqual(r["status"], "differ")
        self.assertEqual(sw["aram"]["dmg_dealt"], CHAMP["Swain"]["aram"]["dmg_dealt"])

    def test_cooldown_list_and_linear_range(self):
        th = copy.deepcopy(CHAMP["Thresh"])
        w = next(s for s in th["spells"] if s["slot"] == "W")
        w["cooldown"] = [21, 20, 19, 18, 17]
        self.assertEqual(check_champion(th, ["W - 어둠의 통로 재사용 대기시간: 21/20/19/18/17초 ⇒ 19/18/17/16/15초"])[0]["status"], "applied")
        self.assertEqual(w["cooldown"], [19, 18, 17, 16, 15])
        ga = copy.deepcopy(CHAMP["Garen"])
        gw = next(s for s in ga["spells"] if s["slot"] == "W")
        gw["cooldown"] = [22, 19.5, 17, 14.5, 12]
        check_champion(ga, ["W - 용기 재사용 대기시간: 22~12초 ⇒ 18~12초"])
        self.assertEqual(gw["cooldown"], [18, 16.5, 15, 13.5, 12])

    def test_current_data_has_valid_checks(self):
        """지금 데이터의 대조 결과가 정해진 값만 쓰는지 (이전 값·불일치는 오류가 아니라 확인 이슈로 간다)"""
        for x in DATA["champions"] + DATA["augments"]:
            for r in x.get("patchCheck") or []:
                self.assertIn(r["status"], ("ok", "applied", "stale", "differ", "unknown"), x["name"])

    def test_augment_unique_datavalue(self):
        spell = {"DataValues": [{"name": "Cooldown", "values": [15.0] * 3}, {"name": "Other", "values": [2.0] * 3}]}
        res, patched = check_augment(spell, "이 효과는 15초의 재사용 대기시간이 적용됩니다.", ["재사용 대기시간: 15초 ⇒ 5초"])
        self.assertEqual(res[0]["status"], "applied")
        self.assertEqual(next(d for d in patched["DataValues"] if d["name"] == "Cooldown")["values"], [5.0] * 3)

    def test_presence_notation(self):
        self.assertEqual(presence({30.0, 90.0, 25.0, 15.0}, "75~225 + 0.6", "90~270 + 0.75 + 0.45", "3초 동안 부착 피해"), "ok")
        self.assertEqual(presence(set(), "", "30/40/50", "", "30~50(레벨 비례)"), "ok")


class AutoAndWiki(unittest.TestCase):
    def test_auto_stats_conservative(self):
        self.assertEqual(B.auto_stats("치명타 확률이 25% 증가합니다.")[0], {"CRIT": 0.25})
        self.assertEqual(B.auto_stats("기본 공격 시 공격 속도가 30% 증가합니다.")[0], {})  # 조건
        self.assertEqual(B.auto_stats("궁극기 스킬 가속이 30 증가합니다.")[0], {})  # 대상 한정
        self.assertEqual(B.auto_stats("이동 속도가 100% 증가합니다. 피해를 입은 후 6초 동안은 효과가 멈춥니다.")[0], {})

    def test_auto_matches_hand_rules(self):
        R = DATA["rules"]["augments"]
        for a in DATA["augments"]:
            st, _ = B.auto_stats(a["desc"])
            hand = (R.get(a["id"]) or {})
            if st and not hand.get("auto"):
                for k, v in st.items():
                    self.assertAlmostEqual(hand["statsResolved"][k], v, places=3, msg=a["name"])

    def test_wiki_check(self):
        self.assertEqual(B.wiki_check({"AS": 1.0}, "Additionally, you gain {{as|100% '''bonus''' attack speed}}."), {"AS": True})
        self.assertEqual(B.wiki_check({"AS": 0.5}, "you gain {{as|100% bonus attack speed}}"), {"AS": False})


class Basis(unittest.TestCase):
    def test_every_literal_has_basis(self):
        warn = []
        for aid, r in json.loads((ROOT / "rules.json").read_text(encoding="utf-8"))["augments"].items():
            a = next((x for x in DATA["augments"] if x["id"] == aid), None)
            if a and r.get("stats"):
                B.rule_basis(r, DATA["rules"]["augments"][aid].get("statsResolved") or {}, a["desc"], warn, a["name"])
        self.assertEqual(warn, [])


class Logs(unittest.TestCase):
    RAW = {"activePlayer": {"riotId": "나#KR1", "summonerName": "나"},
           "allPlayers": [{"riotId": "나#KR1", "riotIdGameName": "나", "riotIdTagLine": "KR1"},
                          {"riotId": "적#1", "riotIdGameName": "적", "riotIdTagLine": "1"}],
           "events": {"Events": [{"KillerName": "적", "VictimName": "나", "Assisters": ["적"]}]}}

    def test_redact(self):
        out = json.dumps(desktop.redact_names(self.RAW), ensure_ascii=False)
        for n in ("나#", "적#", "KR1", '"나"', '"적"'):
            self.assertNotIn(n, out)
        self.assertIn("플레이어0", out)
        self.assertIn("플레이어1", out)

    def test_prune(self):
        d = pathlib.Path(tempfile.mkdtemp())
        for i in range(35):
            f = d / f"game_{i:02d}.json"
            f.write_text("{}")
            os.utime(f, (time.time() - 100 + i,) * 2)
        desktop.prune_logs(d, 30)
        left = sorted(p.name for p in d.glob("game_*.json"))
        self.assertEqual(len(left), 30)
        self.assertNotIn("game_00.json", left)


class DataSanity(unittest.TestCase):
    def test_big_drop_blocks_refresh(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import data_sanity
        old = {"champions": [{"id": "A", "name": "a"}] * 170, "augments": [{}] * 215, "items": [{}] * 300}
        self.assertEqual(data_sanity.problems(old, old), [])
        self.assertEqual(data_sanity.problems(old, {**old, "augments": [{}] * 210}), [], "패치로 몇 개 빠지는 건 괜찮다")
        self.assertTrue(data_sanity.problems(old, {**old, "augments": [{}] * 100}), "절반으로 줄면 막는다")
        self.assertTrue(data_sanity.problems(old, {**old, "items": []}))


class LockfileCheck(unittest.TestCase):
    def test_bad_lockfile_rejected(self):
        import tempfile
        from pathlib import Path
        import lcu
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lockfile"
            for bad in ("L:1:abc:pw:https", "L:1:99999:pw:https", "L:1:2999:pw:ftp"):
                p.write_text(bad, encoding="utf-8")
                with self.assertRaises(ValueError, msg=bad):
                    lcu.Lcu(str(p))
            p.write_text("L:1:2999:pw:https", encoding="utf-8")
            self.assertEqual(lcu.Lcu(str(p)).base, "https://127.0.0.1:2999")


class Lcu(unittest.TestCase):
    def test_clean_item_set(self):
        self.assertEqual(lcu.clean_item_set("222", "x" * 99, [{"type": "a", "items": ["3031"]}])[1], "x" * 60)
        # 롤 상점은 이모지를 '?' 로 그린다 → 빼고 보낸다
        _, _, out = lcu.clean_item_set("222", "t", [{"type": "🎯 기본 빌드 (탱커형)", "items": [1]},
                                                    {"type": "🔥 강철 같은 심장 고르면", "items": [1]}, {"type": "❤️ 변칙", "items": [1]}])
        self.assertEqual([b["type"] for b in out], ["기본 빌드 (탱커형)", "강철 같은 심장 고르면", "변칙"])
        for bad in (("abc", "t", [{"items": [1]}]), ("222", "t", [{"items": ["1;x"]}]), ("222", "t", []),
                    (None, "t", [{"items": [1]}]), ("222", "t", ["x"]), ("222", "t", [{"items": "12"}])):
            with self.assertRaises(ValueError):
                lcu.clean_item_set(*bad)

    def _client(self, get_reply):
        c = lcu.Lcu.__new__(lcu.Lcu)
        c.puts = []
        c.summoner_id = lambda: (123, 456)

        def req(method, path, body=None):
            if method == "GET":
                return get_reply
            c.puts.append(body)
        c.req = req
        return c

    def test_current_champion_from_session_first(self):
        c = lcu.Lcu.__new__(lcu.Lcu)
        replies = {"/lol-champ-select/v1/session": {"localPlayerCellId": 2, "myTeam": [{"cellId": 1, "championId": 39},
                                                                                     {"cellId": 2, "championId": 799}]},
                   "/lol-champ-select/v1/current-champion": 39}  # 대기석에서 바꾼 뒤에도 앞 챔피언으로 남는 경우
        c.req = lambda m, path, body=None: replies[path]
        self.assertEqual(c.current_champion(), 799)
        replies["/lol-champ-select/v1/session"] = {}
        self.assertEqual(c.current_champion(), 39)

    def test_keeps_user_sets_and_backs_up(self):
        c = self._client({"itemSets": [{"uid": "mine", "title": "내 세트"}], "accountId": 456})
        bd = tempfile.mkdtemp()
        c.write_item_set(222, "t", [{"type": "a", "items": [3031]}], backup_dir=bd)
        titles = [s["title"] for s in c.puts[0]["itemSets"]]
        self.assertEqual(titles, ["t", "내 세트"])
        self.assertEqual(len(os.listdir(bd)), 1)

    def test_old_name_sets_replaced(self):
        """저장소 이름을 바꾸기 전(aram-augment-tool-)에 등록한 세트도 우리 것으로 보고 교체"""
        c = self._client({"itemSets": [{"uid": "aram-augment-tool-222", "title": "예전"}, {"uid": "mine", "title": "내 세트"}]})
        c.write_item_set(222, "새", [{"type": "a", "items": [3031]}])
        sets = c.puts[0]["itemSets"]
        self.assertEqual([s["title"] for s in sets], ["새", "내 세트"])
        self.assertTrue(sets[0]["uid"].startswith("kalbaram-addon-"))

    def test_no_write_when_read_fails(self):
        for reply in (None, {}, {"itemSets": None}):
            c = self._client(reply)
            with self.assertRaises(RuntimeError):
                c.write_item_set(222, "t", [{"type": "a", "items": [3031]}])
            self.assertEqual(c.puts, [], "읽기 실패면 저장(PUT) 안 함")


class Settings_stub:
    def get(self, k, default=None):
        return default


class Desktop(unittest.TestCase):
    def test_numpy_single_thread(self):
        # 아이콘 비교가 모든 코어를 잡아 게임이 끊기지 않게: desktop 을 불러오면 계산 라이브러리 스레드는 1개
        src = (ROOT / "desktop.py").read_text(encoding="utf-8")
        self.assertLess(src.index("OPENBLAS_NUM_THREADS"), src.index("import augocr"))
        self.assertEqual(os.environ.get("OPENBLAS_NUM_THREADS"), "1")

    def test_calc_time_counted_once_per_calc(self):
        api = desktop.Api(Settings_stub())
        for seq in (1, 1, 2):
            api.set_overlay({"calcMs": 30 * seq, "calcSeq": seq})
        sm = api.calc_summary()
        self.assertEqual(sm["n"], 2, "같은 계산을 여러 번 보내도 한 번")
        self.assertEqual(sm["max"], 60)
        self.assertIsNone(api.calc_summary(), "부르면 비운다")

    def test_parse_live_real_game_snapshots(self):
        # 실제 경기 기록(베인 판, 이름 가린 것)이 그대로 읽힌다: 챔피언·레벨·아이템·실제 능력치·적 5명
        folder = ROOT / "tests" / "data" / "games" / "vayne_20261015"
        for f in sorted(folder.glob("live_*s.json")):
            st = desktop.parse_live(json.loads(f.read_text(encoding="utf-8")))
            self.assertEqual(st["champion"], "Vayne", f.name)
            self.assertEqual(len(st["enemies"]), 5, f.name)
            self.assertIn("attackDamage", st["stats"], f.name)
        st = desktop.parse_live(json.loads((folder / "live_1202s.json").read_text(encoding="utf-8")))
        self.assertEqual(st["level"], 18)
        self.assertIn("3031", st["items"])  # 무한의 대검

    def test_statcheck_logged_when_build_changes(self):
        api = desktop.Api(Settings_stub())
        got = []

        class Rec:
            dir = "g1"

            def event(self, kind, data=None, dedupe=False):
                got.append((kind, data))
        api._rec = Rec()
        sc = lambda lv, ad: {"level": lv, "items": ["3031"], "augs": [], "rows": {"AD": [100, ad]}}
        for x in (sc(3, 58), sc(3, 61), sc(4, 61)):  # 같은 레벨·아이템에서 실제 값만 흔들리면(버프) 다시 안 남긴다
            api.set_overlay({"statCheck": x})
        checks = [d for k, d in got if k == "statcheck"]
        self.assertEqual([c["level"] for c in checks], [3, 4])

    def test_live_url_local_only(self):
        old = os.environ.get("ARAM_LIVE_URL")
        try:
            os.environ["ARAM_LIVE_URL"] = "https://evil.example.com/x"
            self.assertEqual(desktop.live_url(), desktop.LIVE_URL_DEFAULT)
            os.environ["ARAM_LIVE_URL"] = "https://127.0.0.1:1234/x"
            self.assertEqual(desktop.live_url(), "https://127.0.0.1:1234/x")
        finally:
            os.environ.pop("ARAM_LIVE_URL", None)
            if old:
                os.environ["ARAM_LIVE_URL"] = old

    def test_api_exposes_only_methods(self):
        api = desktop.Api(desktop.Settings())
        public = [n for n in dir(api) if not n.startswith("_")]
        self.assertTrue(all(callable(getattr(api, n)) for n in public), public)
        self.assertLessEqual(len(public), 18)  # update_status 추가 (데이터 받기 진행 표시)

    def test_version_compare(self):
        self.assertGreater(desktop.version_tuple("0.1.10"), desktop.version_tuple("0.1.9"))


class FakeCanvas:
    """overlay.draw 시험용: 글자가 비면 bbox 가 None (Tk 와 같은 동작)"""
    def __init__(self):
        self.items, self.n = {}, 0

    def delete(self, *a):
        self.items.clear()

    def create_text(self, x, y, text="", **kw):
        self.n += 1
        self.items[self.n] = (x, y, text)
        return self.n

    def create_rectangle(self, *a, **kw):
        self.n += 1
        self.items[self.n] = a
        return self.n

    def bbox(self, i):
        x, y, text = self.items[i]
        return None if not text else (x - 50, y, x + 50, y + 20)

    def tag_raise(self, *a):
        pass


class Overlay(unittest.TestCase):
    def test_draw_handles_empty_label(self):
        import overlay
        payload = {"cards": [{"icon": "", "label": "", "reason": ""}, None,
                             {"icon": "🔥", "label": "강력 추천", "tier": "S", "reason": "잘 맞아요"}],
                   "hud": ["다음 구매: 무한의 대검", "💰 지금 B.F. 대검 (1300G)"]}
        c = FakeCanvas()
        overlay.draw(c, payload, 1600, 900)  # 예외 없이
        self.assertGreater(len(c.items), 3)

    def test_card_reason_stays_above_reroll(self):
        """이유가 길어도 새로고침 버튼(이름 줄 + CARD_BOTTOM) 위에서 끝난다. 글자 크기는 픽셀(음수)이라 화면 배율을 안 탄다 (2026-10-15 지적)"""
        import overlay

        class FontCanvas(FakeCanvas):
            def __init__(self):
                super().__init__()
                self.fonts = {}

            def create_text(self, x, y, text="", font=None, width=None, **kw):
                i = super().create_text(x, y, text=text)
                self.fonts[i], self.width = font, width or 10 ** 6
                return i

            def itemconfig(self, i, font=None, text=None):
                if font:
                    self.fonts[i] = font
                if text is not None:
                    self.items[i] = (*self.items[i][:2], text)

            def bbox(self, i):
                if i not in self.fonts:
                    return super().bbox(i)
                x, y, text = self.items[i]
                px = -self.fonts[i][1]
                lines = -(-len(text) * px // self.width)  # 글자 폭 ≈ 글자 크기
                return None if not text else (x - 50, y, x + 50, y + int(lines * px * 1.4))

        W, H, name_y = 2520, 1575, 0.4019
        reason = "🛡️ 적 대응 · 취약과 잘 맞아요. 🔄 폭딜형으로 변신 · 스킬 한 바퀴 피해가 늘어요 (360 → 564)."
        card = {"icon": "🔥", "label": "강력 추천 · 1순위", "tier": "S", "reason": reason}
        pos = {"y": name_y + overlay.BELOW_NAME, "bottom": name_y + overlay.CARD_BOTTOM, "xs": [0.29, 0.5, 0.72]}
        c = FontCanvas()
        overlay.draw(c, {"cards": [card] * 3, "pos": pos}, W, H, scale=H / 1080)
        self.assertTrue(all(f[1] < 0 for f in c.fonts.values()), "글자 크기는 픽셀")
        reasons = [i for i, v in c.items.items() if len(v) == 3 and v[2] and reason.startswith(v[2].rstrip("…"))]
        self.assertEqual(len(reasons), 3)
        for i in reasons:
            self.assertLessEqual(c.bbox(i)[3] + 4, int(H * pos["bottom"]))
            self.assertTrue(c.items[i][2].startswith("🛡️ 적 대응"), "앞 문장은 남는다")
        short = {**card, "reason": "잘 맞아요."}
        c = FontCanvas()
        overlay.draw(c, {"cards": [short], "pos": pos}, W, H, scale=H / 1080)
        self.assertIn(("Malgun Gothic" if overlay.IS_WINDOWS else "Apple SD Gothic Neo", -round(overlay.FONT_PX[1] * H / 1080)),
                      c.fonts.values(), "짧으면 줄이지 않는다")

    def test_swap_marker_below_box(self):
        """아이템 칸이 다 찼을 때 바꾸기: 상자가 뜰 때만, 상자 밑에 아이콘 (판다 ✕ → 산다)"""
        import overlay
        sw = {"sell": "3153", "buy": "3036", "sellName": "몰락한 왕의 검", "buyName": "도미닉 경의 인사"}
        payload = {"cards": [None] * 3, "hud": ["다음 구매: 무한의 대검"], "swap": sw}
        self.assertEqual(overlay.layout(payload, 1600, 900), [], "상자가 안 뜨면 바꾸기도 안 뜬다")
        items = overlay.layout({**payload, "shop": "dead"}, 1600, 900)
        self.assertEqual([k for k, *_ in items], ["hud", "swap"])

        class C(FakeCanvas):
            images = []

            def tag_lower(self, *a):
                pass
        c = C()
        overlay.draw(c, {**payload, "shop": "dead"}, 1600, 900, icon_dir=str(ROOT / "web" / "icons" / "augment"))
        swap_rect = [v for v in c.items.values() if len(v) == 4 and v[1] > 900 * overlay.BOX_Y]
        self.assertTrue(swap_rect, "상자 밑에 바꾸기 칸")
        if HAS_IMG:
            img = overlay.swap_image(str(ROOT / "web" / "icons" / "item" / "3153.webp"), str(ROOT / "web" / "icons" / "item" / "3036.webp"), 40)
            self.assertEqual(img.size, (100, 40))
            r, g, b = img.getpixel((20, 20))
            self.assertGreater(r, g, "팔 아이템 가운데는 빨간 ✕")

    def test_queue_not_used_when_not_running(self):
        import overlay
        ov = overlay.Overlay()
        for _ in range(100):
            ov.update({"cards": []})
        self.assertEqual(ov.q.qsize(), 0)
        self.assertEqual(ov.payload, {"cards": []})


class Diag(unittest.TestCase):
    """멈춤 진단 기록: 멈춘 기능과 그때 스레드 위치, 다시 움직임, 게임 화면 표시 상태 변화만 남긴다"""

    def test_stale_recover_and_status(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "diag.log"
        beats = {"a": time.time(), "b": None}
        status = {"v": "게임 창 없음"}
        d = desktop.Diag(path, {"a": (lambda: beats["a"], 10), "b": (lambda: beats["b"], 10)},
                         status=lambda: status["v"])
        d.check()
        d.check()  # 같은 상태는 한 번만
        beats["a"] -= 60  # b 는 아직 안 돈 상태(None) → 멈춤으로 보지 않는다
        d.check()
        d.check()  # 계속 멈춰 있어도 한 번만
        beats["a"] = time.time()
        status["v"] = "표시 중 · 테두리 없음 1920×1080"
        d.check()
        status["v"] = "표시 중 · 테두리 없음 2560×1440"  # 크기만 바뀐 건 남기지 않는다
        d.check()
        log = path.read_text(encoding="utf-8")
        self.assertEqual(log.count("게임 화면 표시:"), 2)
        self.assertEqual(log.count("멈춤: a"), 1)
        self.assertNotIn("멈춤: b", log)
        self.assertIn("다시 움직임: a", log)
        self.assertIn("MainThread", log)  # 멈춘 순간 스레드 위치
        self.assertNotIn(str(pathlib.Path.home()), log)  # 사용자 폴더 경로는 ~ 로


class DataFiles(unittest.TestCase):
    def test_prepare_web_replaces_old_or_broken_data(self):
        d = pathlib.Path(tempfile.mkdtemp())
        bundled = {"version": "16.21.1", "format": 2, "dataVersion": "b"}
        old = {"version": "16.20.1", "format": 2, "dataVersion": "a"}
        newer = {"version": "16.22.1", "format": 2, "dataVersion": "c"}
        (d / "b.json").write_text(json.dumps(bundled))
        for mine, keep in ((old, False), (newer, True), (None, False), ({**newer, "format": 1}, False)):
            p = d / "m.json"
            p.write_text(json.dumps(mine) if mine else '{"version": "16.2')  # None → 반쯤 쓰인 파일
            m, b = desktop.data_patch(p), desktop.data_patch(d / "b.json")
            replace = m is None or (b and (m[0] <= b[0] or m[1] < b[1]))
            self.assertEqual(not replace, keep, mine)

    def test_settings_concurrent_writes(self):
        import threading
        st = desktop.Settings.__new__(desktop.Settings)
        st.path = pathlib.Path(tempfile.mkdtemp()) / "settings.json"
        st.v = {}
        ts = [threading.Thread(target=lambda i=i: [st.set(**{f"k{i}": j}) for j in range(30)]) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(json.loads(st.path.read_text())), 8)  # 깨지지 않은 JSON

    def test_data_has_format_and_parts(self):
        self.assertGreaterEqual(DATA.get("format", 0), 2)
        self.assertTrue(DATA["parts"])
        for i in DATA["items"]:
            for c in i.get("from", []):
                self.assertIn(c, DATA["parts"], i["name"])


class DataQuality(unittest.TestCase):
    """매일 갱신이 망가진 데이터를 올리지 않게 (위키를 못 받으면 칼바람 보정이 사라진다 등)"""
    def test_enough_data(self):
        self.assertGreaterEqual(len(DATA["champions"]), 160)
        self.assertGreaterEqual(len(DATA["augments"]), 180)
        self.assertGreaterEqual(sum(1 for c in DATA["champions"] if c.get("aram")), 150, "칼바람 보정(위키)")
        self.assertGreaterEqual(sum(1 for a in DATA["augments"] if a["effects"] or (DATA["rules"]["augments"].get(a["id"]) or {}).get("statsResolved")), 120)

    def test_level_table_offset(self):
        """레벨별 수치 표의 시작 칸 (카타리나 패시브 68~240, 럭스 패시브 20~190 — 위키와 일치)"""
        from lol_calc import Calc
        for cid, calc, want in (("Katarina", "TotalDamage", (68, 240)), ("Lux", "TotalDamage", (20, 190))):
            p = ROOT / ".cache" / "bins" / f"{cid}.bin.json"
            if not p.exists():
                self.skipTest("캐시 없음 (CI)")
            b = json.loads(p.read_text())
            sp = next(v["mSpell"] for k, v in b.items() if isinstance(v, dict) and "Passive" in k
                      and isinstance(v.get("mSpell"), dict) and calc in (v["mSpell"].get("mSpellCalculations") or {}))
            e, _ = Calc(sp).calc(calc, 1)
            self.assertEqual((round(e.lvl[0]), round(e.lvl[1])), want)


class Measure(unittest.TestCase):
    EOG = {"gameId": 123, "gameLength": 900, "gameMode": "KIWI",
           "localPlayer": {"summonerName": "나", "puuid": "abc", "championId": 222, "level": 18,
                           "stats": {"TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": 30000, "TOTAL_DAMAGE_TAKEN": 24000, "TOTAL_HEAL": 3000}},
           "teams": [{"players": [{"summonerName": "나", "puuid": "abc"}, {"summonerName": "적", "puuid": "x"}]}]}

    def test_redact_and_summary(self):
        red = json.dumps(desktop.redact_eog(self.EOG), ensure_ascii=False)
        for n in ('"나"', '"적"', "abc"):
            self.assertNotIn(n, red)
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "eog_123.json").write_text(red)
        m = desktop.measure_summary(d)
        self.assertEqual(m["games"], 1)
        self.assertEqual(m["perMinute"]["TOTAL_DAMAGE_DEALT_TO_CHAMPIONS"]["avg"], 2000.0)

    def test_watcher_saves_once_after_game(self):
        d = pathlib.Path(tempfile.mkdtemp())
        w = desktop.LcuWatcher(d)

        class Fake:
            calls = 0

            def phase(self):
                return "EndOfGame"

            def current_champion(self):
                return 0

            def req(self, method, path, body=None):
                Fake.calls += 1
                return Measure.EOG
        w.client = Fake()
        w.poll()
        w.poll()  # 같은 게임은 한 번만 저장
        files = list(d.glob("eog_*.json"))
        self.assertEqual(len(files), 1)
        self.assertNotIn("abc", files[0].read_text())
        self.assertEqual(desktop.measure_summary(d)["games"], 1)

    def test_bad_format_ignored(self):
        self.assertIsNone(desktop.eog_summary({"gameId": 1}))
        self.assertIsNone(desktop.eog_summary("x"))


class DataIds(unittest.TestCase):
    def test_ids_safe(self):
        for a in DATA["augments"]:
            self.assertRegex(a["id"], r"^[A-Za-z0-9_]{1,80}$")
        for i in DATA["items"]:
            self.assertRegex(i["id"], r"^\d+$")


if __name__ == "__main__":
    unittest.main()


class AugOcr(unittest.TestCase):
    """증강 선택 화면 읽기: 글자 맞추기·카드 나누기·고른 카드 정하기 (윈도우 없이 확인되는 부분)"""

    def setUp(self):
        import augocr
        self.o = augocr
        names = [a["name"] for a in json.loads((ROOT / "web" / "data.json").read_text(encoding="utf-8"))["augments"]]
        self.m = augocr.Matcher(names)

    def test_match_with_ocr_errors(self):
        self.assertEqual(self.m.match("강 타 자")[0], "강타자")  # 한국어 OCR 은 글자마다 띄우기도 한다
        self.assertEqual(self.m.match("정신 변환")[0], "정신 변환")
        self.assertEqual(self.m.match("정신 변화")[0], "정신 변환")  # 한 글자 틀림
        self.assertIsNone(self.m.match("기본 공격 시 최대 체력의")[0])  # 설명 글자는 안 맞는다

    def test_english_name_maps_to_korean(self):
        m = self.o.Matcher([("강타자", "Heavy Hitter"), ("정신 변환", "Mind to Matter"), "눈 폭발"])
        self.assertEqual(m.match("Heavy Hitter")[0], "강타자")
        self.assertEqual(m.match("Mind to Mater")[0], "정신 변환")  # 한 글자 틀려도
        self.assertEqual(m.match("눈 폭발")[0], "눈 폭발")

    def test_words_of_different_height_stay_together(self):
        # 윈도우 가상 PC 실측 단어 상자 (1024x700 창, 이름 줄 y≈322): 높이가 달라도 한 이름으로 묶여야 한다
        m = self.o.Matcher([("눈 폭발", "Snowblast"), ("강타자", "Heavy Hitter"), ("정신 변환", "Mind to Matter")])
        W, H = 1024, 700
        words = [("Snowblast", 220, 313, 88, 18), ("Heavy", 466, 313, 50, 22), ("Hitter", 522, 314, 52, 17),
                 ("Mind", 717, 313, 42, 17), ("to", 765, 316, 16, 14), ("Matter", 787, 313, 58, 17)]
        offers = self.o.find_offers(words, W, H, m)
        self.assertEqual(self.o.slots(offers, W), ["눈 폭발", "강타자", "정신 변환"])

    def test_merged_line_split_into_cards(self):
        W, H = 1920, 1080
        y = int(H * 0.46)
        words = [("눈", 560, y, 30, 30), ("폭발", 595, y, 60, 30), ("강타자", 900, y, 90, 30),
                 ("정신", 1250, y, 60, 30), ("변환", 1315, y, 60, 30), ("저항", 1290, y + 40, 40, 20)]
        offers = self.o.find_offers(words, W, H, self.m)
        self.assertEqual(self.o.slots(offers, W), ["눈 폭발", "강타자", "정신 변환"])
        # 두 장만 읽혔으면 화면 위치로 칸을 정한다
        two = self.o.find_offers(words[2:], W, H, self.m)
        self.assertEqual(self.o.slots(two, W), [None, "강타자", "정신 변환"])
        # 이름 줄 밖(위쪽 글자)은 무시
        self.assertEqual(self.o.find_offers([(t, x, 50, w, h) for t, x, _, w, h in words], W, H, self.m), [])

    def test_card_pos_for_overlay(self):
        import overlay
        W, H = 1920, 1080
        offers = [("눈 폭발", 620, 497), ("강타자", 945, 497), ("정신 변환", 1300, 497)]
        pos = self.o.card_pos(offers, W, H)
        self.assertEqual(pos["xs"], [round(620 / W, 4), round(945 / W, 4), round(1300 / W, 4)])
        self.assertAlmostEqual(pos["nameY"], 497 / H, places=3)
        # 두 장만 읽혀도 빈 칸은 간격으로 채운다
        two = self.o.card_pos([offers[0], offers[2]], W, H)
        self.assertAlmostEqual(two["xs"][1], (620 + 1300) / 2 / W, places=3)
        # 게임 화면 표시는 읽은 카드 자리에 붙는다
        items = overlay.layout({"cards": [{"label": "a"}] * 3, "pos": {**overlay.DEFAULT_POS, "xs": pos["xs"],
                                                                          "y": pos["nameY"] + overlay.BELOW_NAME}}, W, H)
        self.assertEqual([x for _, x, _, _ in items], [620, 945, 1300])
        self.assertEqual(items[0][2], int(H * (497 / H + overlay.BELOW_NAME)))

    def test_click_to_card(self):
        pos = {"xs": [0.29, 0.5, 0.72], "nameY": 0.46}
        self.assertEqual(self.o.card_at(pos, 0.51, 0.40), 1)
        self.assertEqual(self.o.card_at(pos, 0.30, 0.60), 0)
        self.assertIsNone(self.o.card_at(pos, 0.5, 0.70), "카드 밑 새로고침 버튼은 고른 게 아니다")
        self.assertIsNone(self.o.card_at(pos, 0.61, 0.40), "카드 사이 빈 곳")

    def test_tracker_offers_then_pick(self):
        t = self.o.Tracker()
        offers = ["치명적 리듬", "영혼 흡수", "화염 낙인"]
        t.click(1)  # 선택 화면 전 클릭은 무시
        t.feed(offers)
        self.assertEqual(t.state["offers"], offers)
        seq = t.state["seq"]
        t.feed(offers)
        self.assertEqual(t.state["seq"], seq)  # 같은 선택지는 다시 안 보낸다
        t.click(1)  # 영혼 흡수 클릭
        t.feed(None)
        self.assertEqual(t.state["pickSeq"], 0)  # 한 번 못 읽은 걸로는 닫힘 아님
        t.feed(None)
        self.assertEqual((t.state["picked"], t.state["pickSeq"], t.state["offers"]), ("영혼 흡수", 1, None))

    def test_hover_partial_names_keep_screen_open(self):
        # v0.1.13 실제 PC: 마우스를 올리자 이름이 잘려 읽혀 닫힘으로 착각 → 그 뒤 클릭을 못 잡았다
        t = self.o.Tracker()
        offers = ["포로 쇄도", None, "지옥불 난사 발동"]
        t.feed(offers)
        for _ in range(5):
            t.feed(None, partial=True)  # '포로' · '난사 발동' 만 보임
        self.assertEqual(t.state["pickSeq"], 0, "잘린 이름이 보이는 동안은 열린 것")
        t.click(2)
        t.feed(None)
        t.feed(None)
        self.assertEqual(t.state["picked"], "지옥불 난사 발동")
        m = self.o.Matcher(["포로 쇄도", "지옥불 난사 발동"])
        self.assertEqual(m.match("포로 쇄도 저항")[0], "포로 쇄도")
        self.assertTrue(m.partial("포로"))
        self.assertFalse(m.partial("이전 공격과 다른 기본 공격 또는 스킬로 적 챔피언에게 피해를 입히면"))

    def test_cut_name_shared_by_two_augments_is_not_guessed(self):
        # 2026-10-17 사미라 판: 설명 창이 가려 '지옥불'만 읽혔는데 지옥불 영혼으로 짐작 → 그 상태에서 누른 카드가 틀린 증강으로
        m = self.o.Matcher(["지옥불 영혼", "지옥불 난사 발동", "미친 과학자", "우르프의 챔피언"])
        self.assertEqual(m.match("지옥불")[0], None)
        self.assertEqual(m.match("지옥불 난사 발동")[0], "지옥불 난사 발동")
        self.assertEqual(m.match("미친 과학")[0], "미친 과학자", "앞부분이 한 증강뿐이면 그대로")
        t = self.o.Tracker()
        full = ["지옥불 난사 발동", "미친 과학자", "우르프의 챔피언"]
        t.feed(full)
        seq = t.state["seq"]
        t.feed([None, None, "우르프의 챔피언"])  # 설명 창이 가린 읽기
        self.assertEqual((t.state["seq"], t.state["offers"]), (seq, full), "가려진 읽기는 같은 선택지")
        t.click(0)
        t.feed([None, None, "우르프의 챔피언"])
        t.feed(None)
        t.feed(None)
        self.assertEqual(t.state["picked"], "지옥불 난사 발동")

    def test_tracker_first_card_click_is_the_pick(self):
        # 2026-10-17 카이사 판: 광휘의 검 클릭 → 0.6초 뒤 사라지는 옆 카드(빵과 치즈) 클릭 → 빵과 치즈로 알렸다
        t = self.o.Tracker()
        t.feed(["광휘의 검 업그레이드", "빵과 치즈", "마도사의 주스 상자"])
        t.click(0)
        t.click(None)
        t.click(1)
        t.feed(None)
        t.feed(None)
        self.assertEqual(t.state["picked"], "광휘의 검 업그레이드")

    def test_tracker_reroll_resets_click_and_unknown(self):
        t = self.o.Tracker()
        t.feed(["눈 폭발", "강타자", "정신 변환"])
        t.click(1)
        t.feed(["눈 폭발", "탱크 엔진", "정신 변환"])  # 새로고침 → 새 선택지, 앞 클릭 무효
        self.assertEqual(t.state["seq"], 2)
        t.click(None)  # 카드 밖 클릭
        now = [1000.0]
        orig = self.o.time.time
        self.o.time.time = lambda: now[0]
        try:
            t.feed(None)
            t.feed(None)
            self.assertEqual(t.state["pickSeq"], 0, "클릭 없이 닫히면 바로 정하지 않는다 (화면만 내렸을 수 있다)")
            self.assertIsNone(t.state["offers"], "카드 표시는 지운다")
            now[0] += t.REOPEN_WAIT
            t.feed(None)
        finally:
            self.o.time.time = orig
        self.assertEqual(t.state["pickSeq"], 1)
        self.assertIsNone(t.state["picked"])  # 클릭을 못 봤으면 정하지 않는다
        self.assertEqual(t.state["pickedFrom"], ["눈 폭발", "탱크 엔진", "정신 변환"])

    def test_tracker_screen_hidden_then_reopened(self):
        # 2026-10-17 우르곳 판: 첫 선택 화면이 8초 만에 사라졌다 2초 뒤 같은 선택지로 다시 떴다 → '못 정함'을 먼저 알렸다
        t = self.o.Tracker()
        offers = ["강타자", "도주 계획", "불을 밝혀"]
        t.feed(offers)
        t.feed(None)
        t.feed(None)
        t.feed(offers)
        self.assertEqual(t.state["pickSeq"], 0, "같은 선택지가 다시 뜨면 이어서")
        self.assertEqual(t.state["offers"], offers)
        t.click(2)
        t.feed(None)
        t.feed(None)
        self.assertEqual((t.state["picked"], t.state["pickSeq"]), ("불을 밝혀", 1))
        # 다른 선택지(다음 선택 화면)가 뜨면 앞 것은 그때 못 정함으로
        t.feed(["궁극기 봇", "한 발에 두 놈", "열의"])
        t.feed(None)
        t.feed(None)
        t.feed(["검을 뽑아라", "판도라의 상자", "보석 건틀릿"])
        self.assertEqual((t.state["picked"], t.state["pickSeq"], t.state["pickedFrom"]), (None, 2, ["궁극기 봇", "한 발에 두 놈", "열의"]))

class UpdateData(unittest.TestCase):
    """데이터 받기는 별도 프로그램에서 (창이 '응답 없음'이 되지 않게), 진행은 기록 파일로 본다"""

    def test_runs_in_child_and_reports_progress(self):
        d = pathlib.Path(tempfile.mkdtemp())
        orig_user_dir, orig_popen = desktop.user_dir, desktop.subprocess.Popen
        started = []

        class FakeProc:
            returncode = None

            def poll(self):
                return self.returncode

        proc = FakeProc()
        desktop.user_dir = lambda: d
        desktop.subprocess.Popen = lambda cmd, **kw: started.append(cmd) or proc
        try:
            api = desktop.Api(desktop.Settings())
            self.assertTrue(api.update_data()["running"])
            self.assertIn("--update-data", started[0])
            self.assertTrue(api.update_data()["running"])
            self.assertEqual(len(started), 1, "받는 중에 다시 눌러도 한 번만")
            (d / "logs" / "update.log").write_text("패치 버전: 16.20\n  [건너뜀] x\n아이콘 512개 받는 중...\n", encoding="utf-8")
            st = api.update_status()
            self.assertEqual((st["running"], st["step"]), (True, "아이콘 512개 받는 중"))
            proc.returncode = 0
            self.assertEqual(api.update_status()["ok"], True)
        finally:
            desktop.user_dir, desktop.subprocess.Popen = orig_user_dir, orig_popen


class DataFolder(unittest.TestCase):
    """윈도우 데이터 폴더는 프로그램 폴더 안 data (예전 %APPDATA%\\AramAugment 는 옮기고 지운다)"""

    def test_move_old_data(self):
        old, new = pathlib.Path(tempfile.mkdtemp()) / "AramAugment", pathlib.Path(tempfile.mkdtemp()) / "data"
        (old / "logs").mkdir(parents=True)
        (old / "logs" / "game_1.json").write_text("old", encoding="utf-8")
        (old / "logs" / "diag.log").write_text("old", encoding="utf-8")
        (old / "settings.json").write_text('{"on_top": true}', encoding="utf-8")
        (old / "web").mkdir()
        (old / "web" / "data.json").write_text("{}", encoding="utf-8")
        (new / "logs").mkdir(parents=True)
        (new / "logs" / "diag.log").write_text("new", encoding="utf-8")
        desktop.move_old_data(old, new)
        self.assertFalse(old.exists(), "예전 폴더는 지운다")
        self.assertEqual((new / "settings.json").read_text(encoding="utf-8"), '{"on_top": true}')
        self.assertEqual((new / "logs" / "game_1.json").read_text(encoding="utf-8"), "old")
        self.assertEqual((new / "logs" / "diag.log").read_text(encoding="utf-8"), "new", "새 위치에 있는 건 덮어쓰지 않는다")
        self.assertTrue((new / "web" / "data.json").exists())


class LiveEnemies(unittest.TestCase):
    def test_enemies_from_other_team(self):
        raw = json.loads((ROOT / "tests" / "data" / "live_missfortune_4s.json").read_text(encoding="utf-8"))
        st = desktop.parse_live(raw)
        self.assertEqual(st["champion"], "MissFortune")
        self.assertEqual(sorted(st["enemies"]), ["Chogath", "Jax", "Lissandra", "Nidalee", "Velkoz"])  # 내 팀(CHAOS)이 아닌 ORDER

    def test_enemy_builds(self):
        # 적이 산 아이템·레벨 (Tab 점수판 정보): 적 수만큼, 챔피언 순서 그대로
        raw = json.loads((ROOT / "tests" / "data" / "live_missfortune_4s.json").read_text(encoding="utf-8"))
        foe = next(p for p in raw["allPlayers"] if p.get("team") == "ORDER")
        foe["items"] = [{"itemID": 3075}, {"itemID": 3047}]
        st = desktop.parse_live(raw)
        self.assertEqual([b["champion"] for b in st["enemyBuilds"]], st["enemies"])
        b = next(b for b in st["enemyBuilds"] if b["items"])
        self.assertEqual(b["items"], ["3075", "3047"])
        self.assertIn("level", b)

    def test_enemy_spell_augments(self):
        # 소환사 주문을 주는 증강은 공식 데이터에 보인다 (2026-10-15 베인 판 실제 값)
        raw = json.loads((ROOT / "tests" / "data" / "live_missfortune_4s.json").read_text(encoding="utf-8"))
        foes = [p for p in raw["allPlayers"] if p.get("team") == "ORDER"]
        foes[0]["summonerSpells"] = {"summonerSpellOne": {"displayName": "급속 성장", "rawDisplayName": "GeneratedTip_Spell_Augment_GrowthSpurt_DisplayName"},
                                     "summonerSpellTwo": {"displayName": "점멸", "rawDisplayName": "GeneratedTip_SummonerSpell_Augment_ARAM_FlashySpell_DisplayName"}}
        foes[1]["summonerSpells"] = {"summonerSpellOne": {"displayName": "점멸", "rawDisplayName": "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName"}}
        st = desktop.parse_live(raw)
        got = {b["champion"]: b["augments"] for b in st["enemyBuilds"]}
        self.assertIn(["GrowthSpurt", "ARAM_FlashySpell"], got.values())
        self.assertEqual(sum(1 for v in got.values() if v), 1, "보통 주문은 증강이 아님")



try:
    import numpy  # noqa: F401
    import PIL  # noqa: F401
    HAS_IMG = True
except ImportError:
    HAS_IMG = False


@unittest.skipUnless(HAS_IMG, "numpy·Pillow 없음 (윈도우 빌드에만 들어감)")
class HudIcons(unittest.TestCase):
    """증강 칸 아이콘 읽기: 실제 게임 화면(2026-10-14 나피리 판)에서 고른 증강을 찾는다"""

    @classmethod
    def setUpClass(cls):
        import hudicons
        cls.h = hudicons
        cls.names = {a["id"]: a["name"] for a in json.loads((ROOT / "web" / "data.json").read_text(encoding="utf-8"))["augments"]}
        cls.tpl = hudicons.Templates(str(ROOT / "web" / "icons" / "augment"), cls.names)

    def test_real_hud_capture(self):
        import numpy as np
        from PIL import Image
        img = np.asarray(Image.open(ROOT / "tests" / "data" / "hud_naafiri_goldrend_endlesshunt.png").convert("RGB"))
        r = self.h.HudReader(self.tpl)
        r.scan(img, 1359)
        groups = r.scan(img, 1359)
        named = [sorted(self.names[i] for i in g) for g in groups]
        self.assertEqual(named, [["골드 강탈"], ["돌진", "앙파상"]])
        import augocr
        ids, unsure = augocr.resolve_groups(groups, self.names, {"방출 역장", "초월 의식", "앙파상", "골드 강탈"})
        self.assertEqual([self.names[i] for i in ids], ["골드 강탈", "앙파상"], "같은 그림 묶음은 선택지에 떴던 이름으로")
        self.assertEqual(unsure, [])
        ids, unsure = augocr.resolve_groups(groups, self.names, set())
        self.assertEqual(unsure, [["돌진", "앙파상"]], "선택지를 못 봤으면 못 정함")

    def test_recover_when_offers_misread(self):
        # 선택지 이름을 잘못(덜) 읽어 고른 증강(골드 강탈)이 비교 대상에 없었다 → 넓혀 찾기로 채운다
        old = self.h.REGION
        self.h.REGION = (0, 0, 1, 1)
        try:
            r, rect = self._hud_reader("hud_naafiri_goldrend_endlesshunt.png", ["강타자", "정신 변환"], None)
            r._verify["unsure"] = True
            r._hud_job(rect, 0)  # 그 판 선택지(강타자·정신 변환)하고만 → 못 찾음
            self.assertIsNone(r._fix)
            r._verify["tries"] = 0
            import threading as _t
            orig = _t.Thread
            _t.Thread = lambda target, args, **k: type("T", (), {"start": lambda self: target(*args)})()
            try:
                r._hud_at = 0
                r._hud_tick(rect)  # 확인 실패 → 넓혀 찾기 시작 + 한 번
                r._hud_at = 0
                r._hud_tick(rect)
            finally:
                _t.Thread = orig
            self.assertEqual(r._fix and r._fix[0], "골드 강탈")
            # 그림이 같은 묶음(돌진/앙파상)은 넓혀 찾기로 정하지 않는다
        finally:
            self.h.REGION = old

    def test_size_hint(self):
        # 지난 게임 크기부터 찾는다: 맞으면 그 크기로 바로, 틀렸으면(해상도·HUD 크기 바뀜) 여러 크기로 다시
        import numpy as np
        from PIL import Image
        img = np.asarray(Image.open(ROOT / "tests" / "data" / "hud_naafiri_goldrend_endlesshunt.png").convert("RGB"))
        base = self.h.HudReader(self.tpl)
        base.scan(img, 1359)
        want = base.scan(img, 1359)
        for hint in (base.size, 0.2):
            r = self.h.HudReader(self.tpl)
            r.hint = hint
            r.scan(img, 1359)
            self.assertEqual(r.scan(img, 1359), want, f"hint {hint}")
            self.assertEqual(r.size, base.size)

    def test_real_false_positives(self):
        # v0.1.15 실제 게임에서 바닥 타일·보라 단색을 증강으로 읽은 조각 (점수 7.4 · 2.6 이 나왔다) → 아무것도 아니어야
        import numpy as np
        from PIL import Image
        for f in ("hud_false_floor.png", "hud_false_purple.png"):
            img = np.asarray(Image.open(ROOT / "tests" / "data" / f).convert("RGB"))
            r = self.h.HudReader(self.tpl)
            r.scan(img, 1400)
            self.assertEqual(r.scan(img, 1400), [], f)
            self.assertTrue(all(-1 <= sc <= 1 for _, sc, _, _ in r.last), "점수는 -1~1")
        import augocr
        jonya = next(i for i, n in self.names.items() if n == "존야 업그레이드")
        ids, _ = augocr.resolve_groups([(jonya,)], self.names, {"광신자", "도취"}, [0.99])
        self.assertEqual(ids, [], "그 판 선택 화면에 없던 증강은 인정하지 않는다")
        ids, _ = augocr.resolve_groups([(jonya,)], self.names, set(), [0.9])
        self.assertEqual(ids, [], "선택 화면을 못 봤으면 아주 확실할 때만")

    def test_empty_screen_finds_nothing(self):
        import numpy as np
        rng = np.random.default_rng(0)
        img = (rng.random((300, 900, 3)) * 70 + 25).astype("uint8")
        r = self.h.HudReader(self.tpl)
        r.scan(img, 900)
        self.assertEqual(r.scan(img, 900), [])

    def _hud_reader(self, fixture, offers, click):
        """Reader: 증강 칸은 그 판 선택 화면에 떴던 증강하고만 비교한다 (가짜 화면 캡처)"""
        import numpy as np
        from PIL import Image
        import augocr
        img = np.asarray(Image.open(ROOT / "tests" / "data" / fixture).convert("RGB"))
        H, W = 1359, img.shape[1]
        canvas = np.zeros((H, W, 4), np.uint8)
        canvas[: img.shape[0], :, :3] = img[..., ::-1]  # BGRA

        class Cap:
            def grab(self, x, y, w, h, *a):
                return canvas[y:y + h, x:x + w].tobytes()

        r = augocr.Reader(lambda: None, lambda: [])
        r.icon_dir = str(ROOT / "web" / "icons" / "augment")
        r._id_names = self.names
        r._hud_cap = Cap()
        r.offered = set(offers)
        r._verify = {"from": offers, "click": click, "until": time.time() + 30}
        return r, (0, 0, W, H)

    def test_hud_fixes_wrong_click(self):
        # 클릭 짐작(강타자)이 틀렸으면 증강 칸 그림(골드 강탈)으로 고친다
        old = self.h.REGION
        self.h.REGION = (0, 0, 1, 1)
        try:
            r, rect = self._hud_reader("hud_naafiri_goldrend_endlesshunt.png", ["강타자", "골드 강탈", "정신 변환"], "강타자")
            r._hud_job(rect, r._game_n if hasattr(r, "_game_n") else 0)
            r._hud_job(rect, getattr(r, "_game_n", 0))  # 연속 2번 보여야 인정
            self.assertEqual(r._fix, ("골드 강탈", ["강타자", "골드 강탈", "정신 변환"], "강타자"))
            self.assertEqual(len(r.hud.tpl.groups), 3, "그 판에 뜬 증강하고만 비교 (215개 전부 X)")
            r.apply_fix()
            st = r.tracker.state
            self.assertEqual((st["picked"], st["replaces"], st["by"], st["pickSeq"]), ("골드 강탈", "강타자", "hud", 1))
            self.assertIsNone(r._verify)
            # 바닥 타일 조각: 아무것도 아니어야 (엉뚱하게 고치지 않는다)
            r, rect = self._hud_reader("hud_false_floor.png", ["광신자", "도취", "존야 업그레이드"], "광신자")
            r._hud_job(rect, 0)
            r._hud_job(rect, 0)
            self.assertIsNone(r._fix)
        finally:
            self.h.REGION = old

    def test_recover_keeps_known_slots(self):
        # 2026-10-15 베인 판: 증강 칸은 고른 순서대로 찬다. 넓혀 찾기(증강 전체와 비교)가 이미 정한 칸을
        # 그림이 같은 다른 증강(시작부터 끝까지 → 포로 쇄도)으로 읽어, 맞게 짐작한 부메랑 투척을 틀리게 '고쳤다'
        import augocr
        r = augocr.Reader(lambda: None, lambda: [])
        r._id_names = {"V": "취약", "FB": "시작부터 끝까지", "OK": "부메랑 투척", "ED": "끝없는 학살",
                       "UH": "궁극의 히드라", "PC": "포로 쇄도", "SH": "작은 도움"}
        r.offered = {"취약", "시작부터 끝까지", "부메랑 투척", "작은 도움"}
        r._apply_hud([("V",), ("FB",)], {("V",): 0.90, ("FB",): 0.93}, [], [], {("V",): (32, 5), ("FB",): (32, 6)})
        self.assertEqual(r.hud_ids, ["V", "FB"])
        r._picked_names = {"취약", "시작부터 끝까지", "부메랑 투척"}
        r._recover = {"from": ["작은 도움", "부메랑 투척"], "click": "부메랑 투척", "tries": 3, "known": {"V", "FB"}}
        wide = [("UH",), ("PC",), ("OK", "ED")]
        r._apply_hud(wide, {("UH",): 0.924, ("PC",): 0.957, ("OK", "ED"): 0.923}, [], [],
                     {("UH",): (32, 5), ("PC",): (32, 6), ("OK", "ED"): (32, 7)})
        self.assertIsNone(r._fix, "맞게 짐작한 것을 고치지 않는다")
        self.assertIsNone(r._recover, "새 칸에 짐작한 증강 그림이 있으면 확인 끝")
        self.assertEqual(r.hud_ids, ["V", "FB", "OK"], "정해진 칸은 그대로, 새 칸은 선택지에 떴던 이름으로")

    def test_shop_window_image_when_found(self):
        # 상점 창 안 아이템에 표시하려면 실제 상점 모습이 필요하다 → 알아봤을 때 가운데 넓게 한 장 (한 판 최대 SHOP_IMGS, 간격 SHOP_IMG_GAP)
        import augocr

        class Cap:
            def grab(self, x, y, w, h, tw=None, th=None):
                return b"\0" * ((tw or w) * (th or h) * 4)

        class Ocr:
            def words(self, img, tw, th):
                return [("판매", 300, 20, 40, 15), ("되돌리기", 420, 20, 70, 15)]

        class Rec:
            def image(self, kind, img, w, h):
                return f"{kind}.png"
        r = augocr.Reader(lambda: None, lambda: [])
        r.rec, r._changed = Rec(), lambda: None
        got = []
        r._rec = lambda k, d, **kw: got.append((k, d))
        for i in range(6):
            r.scan_shop_only(Cap(), Ocr(), (0, 0, 2520, 1575))
            if i == 2:
                r.shop = None  # 닫았다 다시 연다
        shops = [d for k, d in got if k == "shop"]
        self.assertEqual(shops[0]["image"], "shop.png")
        self.assertEqual(shops[0]["window"], [2520, 1575])
        self.assertNotIn("image", shops[1], "곧바로 다시 열면 또 찍지 않는다")

    @unittest.skipUnless(HAS_IMG, "numpy·Pillow 필요")
    def test_locate_items_synthetic(self):
        # 상점처럼 아이템 아이콘을 격자로 깐 그림: 있는 것은 그 자리를 높은 점수로, 없는 것은 낮은 점수로
        import os
        import numpy as np
        import hudicons
        from PIL import Image
        d = ROOT / "web" / "icons" / "item"
        others = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".webp"))[:24]
        bg = Image.new("RGB", (700, 400), (20, 30, 40))
        for k, iid in enumerate(others):
            if iid in ("3031", "3089"):
                iid = others[0]
            if k == 9:
                iid = "3031"
            bg.paste(Image.open(d / f"{iid}.webp").convert("RGB").resize((48, 48)), (40 + (k % 8) * 70, 40 + (k // 8) * 70))
        res = hudicons.locate_items({i: str(d / f"{i}.webp") for i in ("3031", "3089")}, np.asarray(bg), [44, 48, 53])
        self.assertGreaterEqual(res["3031"]["score"], hudicons.SHOP_SURE)
        self.assertLessEqual(abs(res["3031"]["x"] - 110) + abs(res["3031"]["y"] - 110), 6)
        self.assertLess(res["3089"]["score"], hudicons.SHOP_SURE, "상점에 없는 아이템")

    @unittest.skipUnless(HAS_IMG, "numpy·Pillow 필요")
    def test_shop_items_located_and_logged(self):
        # 시험: 상점 그림에서 추천 아이템 자리를 찾아 기록만 한다 (화면 표시는 아직). 지금은 꺼 둠(SHOP_FIND) → 켰을 때 동작만 본다
        import augocr
        self.addCleanup(setattr, augocr, "SHOP_FIND", augocr.SHOP_FIND)
        augocr.SHOP_FIND = True

        class Cap:
            def grab(self, x, y, w, h, tw=None, th=None):
                return b"\0" * ((tw or w) * (th or h) * 4)

        class Ocr:
            def words(self, img, tw, th):
                return [("판매", 300, 20, 40, 15), ("되돌리기", 420, 20, 70, 15)]

        class Rec:
            def image(self, kind, img, w, h):
                return f"{kind}.png"
        r = augocr.Reader(lambda: None, lambda: [])
        r.rec, r._changed = Rec(), lambda: None
        r.icon_dir = str(ROOT / "web" / "icons" / "augment")
        r.shop_targets = ["3031", "6672"]
        got = []
        r._rec = lambda k, d, **kw: got.append((k, d))
        for _ in range(augocr.SHOP_EVERY):
            r.scan_shop_only(Cap(), Ocr(), (0, 0, 1280, 800))
        for _ in range(200):
            if not getattr(r, "_shop_find_busy", False):
                break
            time.sleep(0.05)
        ev = [d for k, d in got if k == "shopfind"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["want"], ["3031", "6672"])
        self.assertEqual(ev[0]["sure"], [], "빈 화면에선 확신하지 않는다")

    def test_timer_text(self):
        import overlay
        f = ("X", -20, "bold")
        self.assertEqual(overlay.timer_text(12, f), ("12", f, "#ffffff"))
        self.assertEqual(overlay.timer_text(3, f)[2], "#ffd54f", "곧 발동이면 노랑")
        text, f2, _ = overlay.timer_text(65, f)
        self.assertEqual(text, "1:05")
        self.assertLess(abs(f2[1]), 20, "세 자리부터 글자를 줄인다")

    def test_shop_find_dropped_when_game_changes(self):
        # 찾는 동안 새 게임이 시작되면 지난 게임 그림의 결과는 버린다 (새 게임 기록에 안 섞이게)
        import augocr
        import hudicons
        r = augocr.Reader(lambda: None, lambda: [])
        r.icon_dir = str(ROOT / "web" / "icons" / "augment")
        r.shop_targets = ["3031"]
        got = []
        r._rec = lambda k, d, **kw: got.append((k, d))
        real = hudicons.locate_items

        def slow(*a, **kw):
            r._game_n = getattr(r, "_game_n", 0) + 1  # 찾는 중에 새 게임
            return real(*a, **kw)
        hudicons.locate_items = slow
        try:
            r._find_shop_items(b"\0" * (64 * 48 * 4), 64, 48, 48, "shop.png")
            for _ in range(200):
                if not getattr(r, "_shop_find_busy", False):
                    break
                time.sleep(0.05)
        finally:
            hudicons.locate_items = real
        self.assertEqual([k for k, d in got if k == "shopfind"], [])
        self.assertFalse(r._shop_find_busy)

    def test_hud_reads_less_when_settled(self):
        # 증강 칸 읽기는 가장 무겁다 → 고른 걸 다 찾았으면 드물게, 4개 다 찾았으면 안 읽는다 (새로 고르면 그때)
        import augocr
        r = augocr.Reader(lambda: None, lambda: [])
        r.offered = {"a"}
        r._picked_names = {"취약", "치명적 리듬"}
        r.hud_ids = ["A1"]
        self.assertEqual(r._hud_wait(), augocr.HUD_EVERY, "고른 것 중 못 찾은 게 있음")
        r.hud_ids = ["A1", "A2"]
        self.assertEqual(r._hud_wait(), augocr.HUD_SETTLED)
        r.hud_ids = ["A1", "A2", "A3", "A4"]
        self.assertEqual(r._hud_wait(), float("inf"))
        self.assertEqual(r._hud_wait(soon=True), 2, "막 골랐으면 바로 확인")

    def test_shop_top_band_skipped_after_bottom_works(self):
        # 아래 버튼 줄로 상점을 알아본 창 크기에서는 위쪽 띠(큰 그림)를 따로 읽지 않는다
        import augocr
        reads = []

        class Cap:
            def grab(self, x, y, w, h, tw=None, th=None):
                reads.append(y)
                return b"\0" * ((tw or w) * (th or h) * 4)

        class Ocr:
            words_ = [("판매", 300, 20, 40, 15), ("되돌리기", 420, 20, 70, 15)]

            def words(self, img, tw, th):
                return self.words_
        r = augocr.Reader(lambda: None, lambda: [])
        r._changed = lambda: None
        o = Ocr()
        for _ in range(augocr.SHOP_EVERY):  # SHOP_EVERY 번에 한 번 읽는다
            r.scan_shop_only(Cap(), o, (0, 0, 2520, 1575))  # 열림 → 아래 줄로 알아봄
        self.assertTrue(r.shop)
        o.words_ = []
        reads.clear()
        for _ in range(8):
            r.scan_shop_only(Cap(), o, (0, 0, 2520, 1575))
        top_y = int(1575 * augocr.SHOP_BAND[0])
        self.assertNotIn(top_y, reads, "위쪽 띠는 안 읽음")
        self.assertTrue(reads, "아래 줄은 계속 읽음")

    def test_shop_key_reads_shop_right_away(self):
        # 2026-10-17 우르곳 판: 2초 간격 읽기 사이에 상점을 열고 닫아 못 알아봤다 → P·ESC·클릭을 보면 바로 읽는다
        import augocr
        r = augocr.Reader(lambda: None, lambda: [])
        r._changed = lambda: None

        class Mouse:
            def __init__(self, keys=(), clicks=0):
                self.keys, self.clicks = list(keys), clicks

            def pressed(self, vk):
                if vk in self.keys:
                    self.keys.remove(vk)
                    return True
                return False

            def clicked(self):
                if self.clicks:
                    self.clicks -= 1
                    return (1, 1)
                return None
        t0 = time.time()
        r.wait_shop_keys(Mouse(keys=[0x50]), time.time() + 5)
        self.assertLess(time.time() - t0, 1.5, "P 를 보면 2초를 다 기다리지 않는다")
        self.assertTrue(r._shop_now)
        reads = []

        class Cap:
            def grab(self, x, y, w, h, tw=None, th=None):
                reads.append(y)
                return b"\0" * ((tw or w) * (th or h) * 4)

        class Ocr:
            def words(self, img, tw, th):
                return [("되돌리기", 420, 20, 70, 15)]
        r._shop_n = 0  # SHOP_EVERY 번에 한 번 차례가 아니어도
        r.scan_shop_only(Cap(), Ocr(), (0, 0, 2520, 1575))
        self.assertTrue(r.shop, "바로 읽어서 상점을 알아본다")
        self.assertFalse(r._shop_now)
        r._shop_click_at = time.time()
        r.wait_shop_keys(Mouse(clicks=1), time.time() + 0.3)
        self.assertFalse(r._shop_now, "클릭은 SHOP_CLICK_GAP 에 한 번만")
        r._shop_click_at = 0.0
        r.shop = None
        r.wait_shop_keys(Mouse(clicks=1), time.time() + 0.3)
        self.assertFalse(r._shop_now, "상점이 닫혀 있을 때 클릭으로는 안 읽는다 (사미라 판: 싸울 때 클릭마다 215번)")
        r.shop = {"left": 0.4}
        r.wait_shop_keys(Mouse(clicks=1), time.time() + 0.3)
        self.assertTrue(r._shop_now, "열려 있을 때 클릭(닫기)은 바로 읽는다")

    def test_auto_protect_when_slow(self):
        # 느려지면 진단용 그림부터 줄이고(자동 보호), 다시 빨라지면 끈다. 증강 선택 읽기는 건드리지 않는다
        import augocr
        r = augocr.Reader(lambda: None, lambda: [])
        got = []
        r._rec = lambda k, d, **kw: got.append((k, d))
        for _ in range(augocr.SLOW_N):
            r._watch_load(augocr.SLOW_MS * 2)
        self.assertTrue(r.protect)
        self.assertEqual(got[-1][0], "protect")
        for _ in range(augocr.SLOW_N):
            r._watch_load(augocr.SLOW_MS * 0.6)
        self.assertTrue(r.protect, "조금 나아진 정도로는 바로 끄지 않는다 (깜박이지 않게)")
        for _ in range(augocr.SLOW_N):
            r._watch_load(10)
        self.assertFalse(r.protect)
        self.assertEqual(r.perf_summary()["횟수"], {"보호 켬": 1, "보호 끔": 1})

    def test_shop_probe_only_after_purchase(self):
        # 2026-10-15 베인 판: 그림 3번 중 1번은 물약을 마셔 아이템이 사라진 때 (상점과 무관) → 골드가 줄었을 때만
        import augocr
        r = augocr.Reader(lambda: None, lambda: [])
        live = {"items": ["3051", "2003"], "gold": 100}
        r.live_fn = lambda: live
        r._watch_inventory()
        live = {"items": ["3051"], "gold": 100}
        r._watch_inventory()
        self.assertFalse(r._shop_probe, "물약을 마심")
        live = {"items": ["3051", "1036"], "gold": 50}
        r._watch_inventory()
        self.assertTrue(r._shop_probe, "샀음")

    def test_late_hud_fixes_unverified_click(self):
        # 2026-10-15 케일 판: 클릭 짐작(극악무도)의 확인 시간이 끝난 뒤에야 증강 칸에 실제로 고른 것(광휘의 검 업그레이드)이 잡혔다.
        # 확인을 못 한 짐작은 기억해 두고, 그 선택지 중 다른 것이 증강 칸에 나타나면 그걸로 고친다
        import augocr
        r = augocr.Reader(lambda: None, lambda: [])
        by = {n: i for i, n in self.names.items()}
        frm = ["극악무도", "광휘의 검 업그레이드", "영혼의 정화"]
        r._id_names = self.names
        r._picked_names = {"극악무도"}
        r._unverified = [{"from": frm, "click": "극악무도"}]
        r._apply_hud([], {}, [], [])
        self.assertIsNone(r._fix, "증강 칸에 아직 없으면 그대로")
        r._apply_hud([], {}, [by["광휘의 검 업그레이드"]], [])
        self.assertEqual(r._fix, ("광휘의 검 업그레이드", frm, "극악무도"))
        self.assertEqual(r._unverified, [])
        # 짐작한 것이 나중에 나타나면 맞은 것: 고치지 않는다
        r._fix, r._unverified = None, [{"from": frm, "click": "극악무도"}]
        r._apply_hud([], {}, [by["극악무도"]], [])
        self.assertIsNone(r._fix)
        self.assertEqual(r._unverified, [])

    def test_short_name_one_letter_off(self):
        import augocr
        m = augocr.Matcher([a for a in self.names.values()])
        self.assertEqual(m.match("양파상")[0], "앙파상")  # 실제 diag.log 에 남은 글자


class ShopBox(unittest.TestCase):
    """게임 화면 상자는 상점 창이 열려 있을 때만, 상점 옆에 (평소엔 게임을 가리지 않게)"""

    def test_overlay_record_reasons_and_timers(self):
        # 기록: 카드 이유 문장과 증강 쿨타임(고른 시각·발동 시각·체력)을 남긴다. 같은 내용은 한 번만
        class Rec:
            def __init__(self):
                self.ev = []

            def event(self, kind, data=None, dedupe=False):
                if dedupe and self.ev and [e for e in self.ev if e[0] == kind][-1:] == [(kind, data)]:
                    return
                self.ev.append((kind, data))
        api = desktop.Api.__new__(desktop.Api)
        api._ov, api._rec, api._reader = None, Rec(), None
        payload = {"cards": [{"name": "퀸카", "label": "추천", "reason": "🔗 강한 연계 · 이유"}, None, None],
                   "hud": [], "timers": [{"name": "도주 계획", "kind": "hp", "base": 1000.4, "readyAt": 1080.6, "trigHp": 33}]}
        api.set_overlay(payload)
        api.set_overlay(payload)
        kinds = [k for k, _ in api._rec.ev]
        self.assertEqual(kinds, ["shown", "timers"], "같은 내용은 한 번만")
        self.assertEqual(api._rec.ev[0][1]["reasons"][0], "🔗 강한 연계 · 이유")
        self.assertEqual(api._rec.ev[1][1]["timers"][0], {"name": "도주 계획", "kind": "hp", "picked": 1000, "readyAt": 1081, "hp": 33})

    def test_stall_and_resource_record(self):
        # 게임 중 감시가 한참 늦게 돌아오면 '멈춤'으로, 30초마다 자원을 기록한다 (2026-10-15 '중간에 컴퓨터가 멈춤')
        class Rec:
            active = True

            def __init__(self):
                self.ev = []

            def event(self, kind, data=None, dedupe=False):
                self.ev.append((kind, data))
        w = desktop.LiveWatcher.__new__(desktop.LiveWatcher)
        w.rec, w.state = Rec(), {"inGame": True}
        samples = iter([{"cpu": 10.0, "mem": 60}, {"cpu": 25.0, "mem": 61, "sysMem": 70}, {"cpu": 26.0}])
        old = desktop.resource_sample
        desktop.resource_sample = lambda: next(samples)
        try:
            w.watch_resources(1000.0, 3.2)
            w.watch_resources(1030.0, 3.1)
            w.watch_resources(1048.0, 18.0)
        finally:
            desktop.resource_sample = old
        self.assertEqual(w.rec.ev[0], ("res", {"cores": 0.5, "mem": 61, "sysMem": 70}))
        self.assertEqual(w.rec.ev[1], ("stall", {"gap": 18.0, "res": {"cpu": 26.0}}))
        self.assertEqual(len(w.rec.ev), 2, "30초가 안 지났으면 자원 기록은 건너뛴다")
        w.state = {"inGame": False}
        w.watch_resources(1100.0, 60.0)
        self.assertEqual(len(w.rec.ev), 2, "게임 밖에선 기록하지 않는다")

    def test_box_when_dead_or_start(self):
        # 상점 창을 못 알아봐도 죽어 있을 때·시작 직후엔 상자를 띄운다 (2026-10-15 케일 판: 상점 인식 0번 → 상자가 한 번도 안 뜸)
        import overlay

        class Ov:
            def update(self, p):
                self.p = p

        class Reader:
            state = {"shop": None}
        api = desktop.Api.__new__(desktop.Api)
        api._ov, api._rec, api._reader = Ov(), None, Reader()
        api._settings = {}
        api.set_overlay({"cards": [None] * 3, "hud": ["다음 구매: 리치베인"], "box": "dead"})
        self.assertEqual([k for k, *_ in overlay.layout(api._ov.p, 1920, 1080)], ["hud"])
        api.set_overlay({"cards": [None] * 3, "hud": ["다음 구매: 리치베인"], "box": None})
        self.assertEqual(overlay.layout(api._ov.p, 1920, 1080), [], "평소엔 안 띄운다")

    def test_timers_redraw_alone(self):
        # 쿨타임은 1초마다 숫자가 바뀐다 → 그 부분만 지우고 다시 그릴 수 있게 전부 "timers" 태그로 그린다
        import overlay

        class C:
            def __init__(self):
                self.tags = []

            def _add(self, *a, **kw):
                self.tags.append(kw.get("tags"))

            create_text = create_rectangle = create_image = _add
        c = C()
        overlay.draw_timers(c, [("PromQueen", "퀸카", 25, 20, False), ("X", "도주 계획", 0, 0, True)], 10, 10, 1080, None)
        self.assertTrue(c.tags)
        self.assertEqual(set(c.tags), {"timers"})
        self.assertEqual(c.timer_images, [], "쿨타임 그림은 따로 붙잡는다 (다시 그릴 때 쌓이지 않게)")

    def test_overlay_perf_summary(self):
        import overlay
        o = overlay.Overlay()
        o._perf_add("그리기", time.time() - 0.01)
        sm = o.perf_summary()
        self.assertEqual(sm["그리기"]["n"], 1)
        self.assertGreaterEqual(sm["그리기"]["max"], 5)
        self.assertEqual(o.perf_summary(), {}, "부르면 비운다")

    def test_timer_icons(self):
        # 증강 쿨타임 아이콘: 표시 창이 스스로 센다. 자동 주기는 고른 시각 기준, 체력 발동은 다시 쓸 수 있는 시각 기준 (모두 예상)
        import overlay
        now = 1000.0
        timers = [{"id": "PromQueen", "name": "퀸카", "kind": "auto", "period": 35, "base": now - 10},
                  {"id": "EscapePlan", "name": "도주 계획", "kind": "hp", "cd": 75, "below": 0.35, "readyAt": now + 40.2},
                  {"id": "ARAM_DawnbringersResolve", "name": "빛의 인도자 결의", "kind": "hp", "cd": 45, "below": 0.5, "readyAt": 0}]
        st = overlay.timer_state(timers, now)
        self.assertEqual([(n, secs, ready) for _, n, secs, _, ready in st],
                         [("퀸카", 25, False), ("도주 계획", 41, False), ("빛의 인도자 결의", 0, True)])
        self.assertEqual(st[0][3], round(overlay.TIMER_STEPS * 25 / 35), "덮개 = 남은 비율")
        self.assertEqual(overlay.timer_state([], now), [])
        items = overlay.layout({"timers": timers}, 1920, 1080)
        self.assertEqual([k for k, *_ in items], ["timers"], "쿨타임만 있어도 표시 창이 뜬다 (아이콘 크기만큼)")
        # 게임을 가리지 않는 크기: 1080p 32px, 창 모드(작은 창)는 작게, 큰 화면도 40px 까지만
        self.assertEqual(overlay.timer_size(1080), 32)
        self.assertEqual(overlay.timer_size(600), 24)
        self.assertEqual(overlay.timer_size(2160), 40)

    def test_timer_image(self):
        # 덮개: 남은 만큼 어둡고 12시부터 시계 방향으로 걷힌다. 아이콘 파일이 없어도 그림은 만든다
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow 없음")
        import overlay
        icon = Path(__file__).resolve().parent.parent / "web" / "icons" / "augment" / "PromQueen.webp"
        full = overlay.timer_image(str(icon), 32, overlay.TIMER_STEPS)
        clear = overlay.timer_image(str(icon), 32, 0)
        quarter = overlay.timer_image(str(icon), 32, overlay.TIMER_STEPS * 3 // 4)  # 1/4 지남 → 오른쪽 위만 밝다
        lum = lambda im, xy: sum(im.getpixel(xy))
        self.assertLess(lum(full, (24, 8)), lum(clear, (24, 8)))
        self.assertEqual(lum(quarter, (24, 8)), lum(clear, (24, 8)), "오른쪽 위(지난 부분)는 걷힘")
        self.assertEqual(lum(quarter, (8, 24)), lum(full, (8, 24)), "왼쪽 아래(남은 부분)는 덮여 있다")
        self.assertEqual(overlay.timer_image("없는파일.webp", 32, 0).size, (32, 32))

    def test_find_shop_and_layout(self):
        import augocr
        import overlay
        W, H = 1600, 900
        words = [("추천", 560, 170, 40, 20), ("모든", 700, 170, 40, 20), ("아이템", 745, 170, 60, 20),
                 ("아이템", 880, 170, 60, 20), ("세트", 945, 170, 40, 20)]
        shop = augocr.find_shop(words, W, H)
        self.assertAlmostEqual(shop["left"], 560 / W, places=3)
        self.assertIsNone(augocr.find_shop([("모든", 700, 170, 40, 20), ("공격", 760, 170, 40, 20)], W, H))
        self.assertIsNotNone(augocr.find_shop([("Recommended", 560, 170, 90, 20), ("All", 700, 170, 30, 20), ("Items", 735, 170, 50, 20),
                                               ("Item", 880, 170, 40, 20), ("Sets", 925, 170, 40, 20)], W, H))
        # 2026-10-15 베인 판 (2520x1575): 상점 아래쪽 버튼 줄 '판매 · 되돌리기 · 1400'
        bottom = augocr.find_shop([("판매", 1000, 1278, 70, 30), ("되돌리기", 1230, 1278, 120, 30), ("1400", 1420, 1278, 60, 30)], 2520, 1575)
        self.assertIsNotNone(bottom)
        self.assertAlmostEqual(bottom["top"], 1278 / 1575, places=3)
        self.assertIsNone(augocr.find_shop([("판매", 1000, 1278, 70, 30)], 2520, 1575), "판매 한 단어만으론 아님")
        # 2026-10-17 카이사 판 놓친 상점 3번: 판매는 흐려서 못 읽고, 탭은 '보든 아이e'·'아이템 서로'로 읽혔다
        self.assertIsNotNone(augocr.find_shop([("되돌리기", 1230, 1278, 120, 30), ("204", 1420, 1278, 60, 30)], 2520, 1575))
        self.assertIsNotNone(augocr.find_shop([("보든", 700, 300, 40, 20), ("아이e", 745, 300, 60, 20), ("상위", 1300, 300, 40, 20),
                                               ("아이템", 1345, 300, 60, 20)], 2520, 1575))
        self.assertIsNone(augocr.find_shop([("산", 10, 100, 20, 20), ("아이템.", 35, 100, 60, 20), ("크라겐", 100, 100, 60, 20)], 2520, 1575),
                          "우리 상자의 '산 아이템' 글자는 상점 아님")
        payload = {"cards": [None] * 3, "hud": ["다음 구매: 크라켄 학살자"]}
        self.assertEqual(overlay.layout(payload, W, H), [], "상점이 안 열려 있으면 상자를 띄우지 않는다")
        items = overlay.layout({**payload, "shop": shop}, W, H)
        kind, x, y, (lines, anchor) = items[0]
        self.assertEqual((kind, anchor, x), ("hud", "nw", overlay.BOX_X), "왼쪽 위 고정")
        moved = overlay.layout({**payload, "shop": {**shop, "left": 0.51, "right": 0.72}}, W, H)
        self.assertEqual(moved[0][1:3], items[0][1:3], "상점 위치가 달라져도 상자는 안 움직인다")


class GameLog(unittest.TestCase):
    """한 판 기록: 폴더 하나에 순서별 기록·화면 조각, 끝나면 zip, 최근 몇 판만"""

    def test_record_one_game(self):
        import zipfile
        import gamelog
        base = pathlib.Path(tempfile.mkdtemp())
        rec = gamelog.GameRecorder(base, keep=2, max_images=2)
        rec.event("ignored", {"x": 1})  # 게임 전에는 안 남는다
        rec.start("Naafiri", {"enemies": ["Lux"]})
        rec.event("offers", {"read": ["양파상"], "offers": ["메아리 시전", None, None]})
        rec.event("shown", {"box": ["다음 구매: 크라켄"]}, dedupe=True)
        rec.event("shown", {"box": ["다음 구매: 크라켄"]}, dedupe=True)  # 같은 내용은 한 번만
        bgra = bytes([10, 20, 30, 255]) * (4 * 3)
        if HAS_IMG:
            self.assertEqual(rec.image("card", bgra, 4, 3), "card_01.png")
            rec.image("card", bgra, 4, 3)
            self.assertIsNone(rec.image("card", bgra, 4, 3), "한 판 그림 한도")
        folder = rec.dir
        rec.finish()
        ev = json.loads((folder / "events.json").read_text(encoding="utf-8"))
        self.assertEqual([e["kind"] for e in ev], ["start", "offers", "shown", "end"])
        self.assertEqual(ev[0]["enemies"], ["Lux"])
        names = zipfile.ZipFile(str(folder) + ".zip").namelist()
        self.assertIn(f"{folder.name}/events.json", names)
        for i in range(3):  # 최근 2판만 남는다
            time.sleep(1.05)
            rec.start(f"C{i}")
            rec.finish()
        self.assertEqual(len([p for p in base.iterdir() if p.is_dir()]), 2)
        self.assertEqual(len(list(base.glob("*.zip"))), 2)


class LowResource(unittest.TestCase):
    def test_one_mode_low_resource(self):
        # 모드는 하나: 게임 중 CPU 상한이 게임 밖보다 낮고, 화면 엔진은 저사양 옵션
        self.assertFalse(hasattr(desktop, "light_mode"))
        self.assertLess(desktop.CPU_CAP["game"], desktop.CPU_CAP["idle"])
        self.assertIn("--disable-gpu", desktop.WEBVIEW2_ARGS)

    def test_child_priority_safe(self):
        # 화면 엔진 우선순위 바꾸기: 윈도우가 아니면 아무것도 안 하고 0, 게임 시작·끝에 연결돼 있다
        if sys.platform != "win32":
            self.assertEqual(desktop.child_priority(True), 0)
        from pathlib import Path
        src = Path(desktop.__file__).read_text(encoding="utf-8")
        self.assertIn("child_priority(True)", src)
        self.assertIn("child_priority(False)", src)
