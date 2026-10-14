"""파이썬 쪽 테스트: python -m unittest discover -s tests

패치 노트 대조, 자동 추정·위키 대조, 근거, 기록 이름 가리기, 아이템 세트 저장 보호, 주소 제한.
네트워크·롤 클라이언트·GUI 없이 돈다 (pywebview 가 없으면 가짜 모듈로 대신한다).
"""
import copy
import json
import os
import pathlib
import sys
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


class Desktop(unittest.TestCase):
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

    def test_tracker_reroll_resets_click_and_unknown(self):
        t = self.o.Tracker()
        t.feed(["눈 폭발", "강타자", "정신 변환"])
        t.click(1)
        t.feed(["눈 폭발", "탱크 엔진", "정신 변환"])  # 새로고침 → 새 선택지, 앞 클릭 무효
        self.assertEqual(t.state["seq"], 2)
        t.click(None)  # 카드 밖 클릭
        t.feed(None)
        t.feed(None)
        self.assertIsNone(t.state["picked"])  # 클릭을 못 봤으면 정하지 않는다
        self.assertEqual(t.state["pickedFrom"], ["눈 폭발", "탱크 엔진", "정신 변환"])

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

    def test_empty_screen_finds_nothing(self):
        import numpy as np
        rng = np.random.default_rng(0)
        img = (rng.random((300, 900, 3)) * 70 + 25).astype("uint8")
        r = self.h.HudReader(self.tpl)
        r.scan(img, 900)
        self.assertEqual(r.scan(img, 900), [])

    def test_short_name_one_letter_off(self):
        import augocr
        m = augocr.Matcher([a for a in self.names.values()])
        self.assertEqual(m.match("양파상")[0], "앙파상")  # 실제 diag.log 에 남은 글자


class ShopBox(unittest.TestCase):
    """게임 화면 상자는 상점 창이 열려 있을 때만, 상점 옆에 (평소엔 게임을 가리지 않게)"""

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
        payload = {"cards": [None] * 3, "hud": ["다음 구매: 크라켄 학살자"]}
        self.assertEqual(overlay.layout(payload, W, H), [], "상점이 안 열려 있으면 상자를 띄우지 않는다")
        items = overlay.layout({**payload, "shop": shop}, W, H)
        kind, x, y, (lines, anchor) = items[0]
        self.assertEqual((kind, anchor), ("hud", "ne"))
        self.assertLess(x, 560, "상점 창 왼쪽 바깥")


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
