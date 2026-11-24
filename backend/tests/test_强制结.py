"""进站无出强制结 主链测试（全局口径：虚出成对）。

夹具三幕：
  ① 在途强制结——甲有虚出行程且开腿已成对（乙超时旁路无行程且开腿非在途，两口径可区分）
  ② 后再真实出站——不得第二行程（真实出站转出站孤儿旁路）
  ③ 强制结与出站并发——不得双成对（开腿 CAS 裁决唯一胜者）

运行：cd backend && python3 -m unittest tests.test_强制结 -v
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import app.db
from app import seed
from app.db import 夹具目录, 连接
from app.services import 强制结, 贯验, 装载


def 换新库() -> None:
    """每用例类一座全新库，互不污染。"""
    tmp = tempfile.mkdtemp(prefix="闸对强制结测-")
    app.db.库路径 = Path(tmp) / "测.db"
    seed.初始化库()


def 装夹具(名: str) -> dict:
    return 装载.装一批(json.loads((夹具目录 / f"{名}.json").read_text(encoding="utf-8")))


def 取行(sql: str, 参: tuple = ()):
    conn = 连接()
    try:
        return conn.execute(sql, 参).fetchone()
    finally:
        conn.close()


def 取列(sql: str, 参: tuple = ()):
    conn = 连接()
    try:
        return conn.execute(sql, 参).fetchall()
    finally:
        conn.close()


class 强制结主链(unittest.TestCase):
    """夹具①②：在途强制结 → 后再真实出站。"""

    @classmethod
    def setUpClass(cls):
        换新库()

    def test_01_装载在途样例(self):
        报 = 装夹具("强制结在途样例")
        self.assertEqual(报["开腿"], 3)          # 卡甲/卡乙/卡丙 各开一腿
        self.assertEqual(报["成对"], 1)          # 卡丙 正常成对
        self.assertEqual(报["超时旁路"], 1)      # 卡乙 缺腿超时旁路
        甲 = 取行("SELECT * FROM 开腿账 WHERE 开腿键='腿-f1'")
        self.assertEqual(甲["状态"], "在途")      # 卡甲 留在途，待强制结
        乙 = 取行("SELECT * FROM 开腿账 WHERE 开腿键='腿-f2'")
        self.assertEqual(乙["状态"], "已旁路")    # 卡乙 超时已旁路

    def test_02_在途强制结_虚出成对(self):
        r = 强制结.执行强制结("腿-f1", 操作员="调度01", 原因="闭站清客",
                            事件时="2026-11-22T08:50:00Z")
        self.assertFalse(r["幂等"])
        self.assertTrue(r["valid"], r["问题"])
        self.assertEqual(r["方式"], "虚出成对")
        self.assertEqual(r["行程键"], "行-f1-虚出-腿-f1")
        # 甲：有虚出行程且开腿已成对
        甲 = 取行("SELECT * FROM 开腿账 WHERE 开腿键='腿-f1'")
        self.assertEqual(甲["状态"], "已成对")
        行程 = 取行("SELECT * FROM 行程账 WHERE 行程键=?", (r["行程键"],))
        self.assertIsNotNone(行程)
        self.assertEqual(行程["出站事件编号"], "虚出-腿-f1")
        self.assertEqual(行程["历时秒"], 3000)
        虚 = 取行("SELECT * FROM 刷卡点 WHERE 事件编号='虚出-腿-f1'")
        self.assertEqual(虚["方向"], "出站")
        self.assertEqual(虚["命运"], "已成对")
        self.assertEqual(虚["批次编号"], "结-腿-f1")  # 虚出可审计
        进 = 取行("SELECT * FROM 刷卡点 WHERE 事件编号='f1'")
        self.assertEqual(进["命运"], "已成对")
        # 乙：旁路无行程且开腿非在途——与甲的虚出成对口径可区分
        self.assertIsNone(取行("SELECT * FROM 行程账 WHERE 卡号='卡乙'"))
        self.assertEqual(取行("SELECT * FROM 开腿账 WHERE 开腿键='腿-f2'")["状态"], "已旁路")
        self.assertEqual(强制结.读强制结("腿-f2"), [])  # 乙无强制结记录
        # 只读接口：开腿键、虚出或旁路、行程键、valid
        读 = 强制结.读强制结("腿-f1")
        self.assertEqual(len(读), 1)
        self.assertTrue(读[0]["valid"])
        self.assertEqual(读[0]["虚出事件编号"], "虚出-腿-f1")
        # 贯验对齐
        贯 = 贯验.跑贯验()
        self.assertTrue(贯["是否通过"], 贯["说明"])
        self.assertEqual(贯["强制结数"], 1)

    def test_03_强制结幂等重放(self):
        r = 强制结.执行强制结("腿-f1", 操作员="调度01")
        self.assertTrue(r["幂等"])
        self.assertTrue(r["valid"])
        self.assertEqual(
            取行("SELECT COUNT(*) AS c FROM 行程账 WHERE 卡号='卡甲'")["c"], 1)
        self.assertEqual(
            取行("SELECT COUNT(*) AS c FROM 强制结账 WHERE 开腿键='腿-f1'")["c"], 1)

    def test_04_后再真实出站_不得第二行程(self):
        报 = 装夹具("强制结后再出站样例")
        self.assertEqual(报["成对"], 0)
        self.assertEqual(报["旁路"], 1)          # 真实出站 → 出站孤儿旁路
        f5 = 取行("SELECT * FROM 刷卡点 WHERE 事件编号='f5'")
        self.assertEqual(f5["命运"], "已旁路")
        孤 = 取行("SELECT * FROM 旁路匣 WHERE 事件编号='f5' AND 原因='出站孤儿'")
        self.assertIsNotNone(孤)
        # 卡甲 仍只有强制结那一笔虚出行程
        self.assertEqual(
            取行("SELECT COUNT(*) AS c FROM 行程账 WHERE 卡号='卡甲'")["c"], 1)
        self.assertTrue(贯验.跑贯验()["是否通过"])

    def test_05_非在途与不存在拒绝(self):
        with self.assertRaises(强制结.强制结异常) as cm:
            强制结.执行强制结("腿-f3")            # 卡丙 已真实成对，无强制结记录
        self.assertEqual(cm.exception.状态码, 409)
        with self.assertRaises(强制结.强制结异常) as cm:
            强制结.执行强制结("腿-不存在")
        self.assertEqual(cm.exception.状态码, 404)
        with self.assertRaises(强制结.强制结异常) as cm:
            强制结.执行强制结("腿-f2", 站点键="鬼站")  # 卡乙已旁路 → 409 先于站点校验
        self.assertEqual(cm.exception.状态码, 409)


class 真实出站先成对(unittest.TestCase):
    """真实出站抢先成对后，强制结必须落败，不得双程。"""

    @classmethod
    def setUpClass(cls):
        换新库()

    def test_出站先成对_强制结409(self):
        装载.装一批({"批次编号": "先-1", "事件": [
            {"事件编号": "g1", "卡号": "卡丁", "站点键": "站A", "方向": "进站",
             "事件时": "2026-11-22T10:00:00Z", "处理时": "2026-11-22T10:00:01Z"},
            {"事件编号": "g2", "卡号": "卡丁", "站点键": "站B", "方向": "出站",
             "事件时": "2026-11-22T10:30:00Z", "处理时": "2026-11-22T10:30:01Z"},
        ]})
        with self.assertRaises(强制结.强制结异常) as cm:
            强制结.执行强制结("腿-g1")
        self.assertEqual(cm.exception.状态码, 409)
        self.assertEqual(
            取行("SELECT COUNT(*) AS c FROM 行程账 WHERE 卡号='卡丁'")["c"], 1)
        self.assertEqual(取行("SELECT COUNT(*) AS c FROM 强制结账")["c"], 0)
        self.assertTrue(贯验.跑贯验()["是否通过"])


class 强制结与出站并发(unittest.TestCase):
    """夹具③：强制结与真实出站并发抢同一开腿——不得双成对。"""

    @classmethod
    def setUpClass(cls):
        换新库()

    def test_并发不得双成对(self):
        for 回 in range(3):
            卡 = f"卡并{回}"
            进, 出 = f"c{回}1", f"c{回}2"
            装载.装一批({"批次编号": f"并-进-{回}", "事件": [
                {"事件编号": 进, "卡号": 卡, "站点键": "站A", "方向": "进站",
                 "事件时": "2026-11-22T12:00:00Z", "处理时": "2026-11-22T12:00:01Z"},
            ]})
            开腿键 = f"腿-{进}"
            闸 = threading.Barrier(2)
            果 = {}

            def 做强制结():
                闸.wait()
                try:
                    果["结"] = 强制结.执行强制结(
                        开腿键, 操作员="并发测", 事件时="2026-11-22T12:30:00Z")
                except 强制结.强制结异常 as e:
                    果["结错"] = e.状态码

            def 做出站():
                闸.wait()
                果["出"] = 装载.装一批({"批次编号": f"并-出-{回}", "事件": [
                    {"事件编号": 出, "卡号": 卡, "站点键": "站C", "方向": "出站",
                     "事件时": "2026-11-22T12:20:00Z", "处理时": "2026-11-22T12:20:01Z"},
                ]})

            线 = [threading.Thread(target=f) for f in (做强制结, 做出站)]
            for t in 线:
                t.start()
            for t in 线:
                t.join()

            with self.subTest(回=回, 果={k: v for k, v in 果.items() if k != "出"}):
                # 唯一胜者：强制结成 或 出站成，恰一笔行程，不得双成对
                self.assertEqual(
                    取行("SELECT COUNT(*) AS c FROM 行程账 WHERE 卡号=?", (卡,))["c"], 1)
                self.assertEqual(
                    取行("SELECT 状态 FROM 开腿账 WHERE 开腿键=?", (开腿键,))["状态"],
                    "已成对")
                结数 = 取行(
                    "SELECT COUNT(*) AS c FROM 强制结账 WHERE 开腿键=?", (开腿键,))["c"]
                出刷 = 取行("SELECT 命运 FROM 刷卡点 WHERE 事件编号=?", (出,))["命运"]
                if 结数 == 1:        # 强制结胜 → 真实出站孤儿旁路
                    self.assertEqual(出刷, "已旁路")
                    self.assertIn("结", 果)
                else:                # 出站胜 → 强制结 409 落败
                    self.assertEqual(出刷, "已成对")
                    self.assertEqual(果.get("结错"), 409)
        self.assertTrue(贯验.跑贯验()["是否通过"])


if __name__ == "__main__":
    unittest.main()
