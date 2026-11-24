"""进站无出强制结：闭站运营异常写闸。

全局选定架构（只此一种，口径分叉位见 方式 字段）：**虚出成对**
  对指定在途开腿执行强制结时，同一事务落：
    开腿账 在途→已成对(CAS) → 虚出刷卡点(方向=出站,命运=已成对) → 行程账(已成对) → 强制结账(审计)
  虚出可审计：强制结账.虚出事件编号 ↔ 刷卡点 ↔ 行程账 三点互连。
  未选「强制旁路无行程」：本口径下强制结后必存在行程行、开腿必非在途。

互斥与并发：
  - 强制结后真实出站 → 无在途开腿 → 出站孤儿旁路，不得第二行程；
  - 强制结与真实出站并发 → 开腿状态 CAS 裁决唯一胜者，败者孤儿旁路/409，不得双成对；
  - 任一步失败整体回滚，不留「在途+行程」分叉半截。
"""
from app.db import 连接
from app.seed import 此刻
from app.services import 时间

口径 = "虚出成对"


class 强制结异常(Exception):
    """写闸拒绝：状态码 + 中文信息。"""

    def __init__(self, 状态码: int, 信息: str):
        super().__init__(信息)
        self.状态码 = 状态码
        self.信息 = 信息


def 执行强制结(
    开腿键: str,
    操作员: str = "",
    原因: str = "",
    事件时: str = "",
    站点键: str = "",
) -> dict:
    """对指定在途开腿落强制结（虚出成对）。同事务四写，幂等可重放。"""
    处理时 = 此刻()
    结事件时 = 事件时 or 处理时
    conn = 连接()
    conn.isolation_level = None  # 手动事务：BEGIN IMMEDIATE 抢占写锁，序列化并发写闸
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            leg = conn.execute(
                "SELECT * FROM 开腿账 WHERE 开腿键=?", (开腿键,)
            ).fetchone()
            if not leg:
                raise 强制结异常(404, f"开腿不存在:{开腿键}")
            if leg["状态"] != "在途":
                旧 = conn.execute(
                    "SELECT * FROM 强制结账 WHERE 开腿键=?", (开腿键,)
                ).fetchone()
                if 旧:
                    conn.execute("COMMIT")
                    return {**校一行(conn, 旧), "幂等": True}
                raise 强制结异常(
                    409, f"开腿{开腿键}状态={leg['状态']}，非在途，不可强制结"
                )
            出站站点键 = 站点键 or leg["进站站点键"]
            if not conn.execute(
                "SELECT 1 FROM 站点 WHERE 站点键=?", (出站站点键,)
            ).fetchone():
                raise 强制结异常(422, f"虚出站点不存在:{出站站点键}")

            强制结键 = f"结-{开腿键}"
            虚出事件编号 = f"虚出-{开腿键}"
            行程键 = f"行-{leg['进站事件编号']}-{虚出事件编号}"

            # ① CAS 抢开腿：在途→已成对。并发下唯一胜者。
            抢 = conn.execute(
                "UPDATE 开腿账 SET 状态='已成对', 更新处理时=? WHERE 开腿键=? AND 状态='在途'",
                (处理时, 开腿键),
            )
            if 抢.rowcount == 0:
                raise 强制结异常(409, f"开腿{开腿键}已被并发关闭，强制结落败")
            # ② 虚出刷卡点：方向=出站、命运=已成对，批次位记强制结键可审计。
            conn.execute(
                """INSERT INTO 刷卡点(事件编号,卡号,站点键,方向,事件时,处理时,命运,行程键,批次编号)
                   VALUES (?,?,?,?,?,?, '已成对', ?, ?)""",
                (虚出事件编号, leg["卡号"], 出站站点键, "出站", 结事件时, 处理时, 行程键, 强制结键),
            )
            # ③ 行程账：虚出成对行程。
            历时 = max(0, 时间.差秒(leg["进站事件时"], 结事件时))
            conn.execute(
                """INSERT INTO 行程账(行程键,卡号,进站事件编号,出站事件编号,进站站点键,出站站点键,
                   进站事件时,出站事件时,历时秒,状态,版本,处理时)
                   VALUES (?,?,?,?,?,?,?,?,?,'已成对',1,?)""",
                (
                    行程键,
                    leg["卡号"],
                    leg["进站事件编号"],
                    虚出事件编号,
                    leg["进站站点键"],
                    出站站点键,
                    leg["进站事件时"],
                    结事件时,
                    历时,
                    处理时,
                ),
            )
            # ④ 进站刷卡点连成对。
            conn.execute(
                "UPDATE 刷卡点 SET 命运='已成对', 行程键=? WHERE 事件编号=?",
                (行程键, leg["进站事件编号"]),
            )
            # ⑤ 强制结账：审计与只读接口之源。
            conn.execute(
                """INSERT INTO 强制结账(强制结键,开腿键,卡号,方式,虚出事件编号,行程键,操作员,原因,处理时)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (强制结键, 开腿键, leg["卡号"], 口径, 虚出事件编号, 行程键,
                 操作员, 原因 or "闭站强制结", 处理时),
            )
            conn.execute("COMMIT")
            return {**校一行(conn, conn.execute(
                "SELECT * FROM 强制结账 WHERE 强制结键=?", (强制结键,)
            ).fetchone()), "幂等": False}
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def 校一行(conn, 行) -> dict:
    """强制结只读视图：开腿键、虚出或旁路、行程键、valid（跨账互证）。"""
    问题 = []
    leg = conn.execute(
        "SELECT * FROM 开腿账 WHERE 开腿键=?", (行["开腿键"],)
    ).fetchone()
    if not leg or leg["状态"] != "已成对":
        问题.append("开腿非已成对")
    t = conn.execute(
        "SELECT * FROM 行程账 WHERE 行程键=?", (行["行程键"],)
    ).fetchone()
    if not t or t["状态"] != "已成对":
        问题.append("行程缺失或非已成对")
    虚 = conn.execute(
        "SELECT * FROM 刷卡点 WHERE 事件编号=?", (行["虚出事件编号"],)
    ).fetchone()
    if not 虚 or 虚["命运"] != "已成对" or 虚["行程键"] != 行["行程键"]:
        问题.append("虚出刷卡点未连成对")
    if leg:
        进 = conn.execute(
            "SELECT * FROM 刷卡点 WHERE 事件编号=?", (leg["进站事件编号"],)
        ).fetchone()
        if not 进 or 进["命运"] != "已成对" or 进["行程键"] != 行["行程键"]:
            问题.append("进站刷卡点未连成对")
    if t and leg and (
        t["进站事件编号"] != leg["进站事件编号"]
        or t["出站事件编号"] != 行["虚出事件编号"]
        or t["卡号"] != 行["卡号"]
    ):
        问题.append("行程与开腿/虚出事件不连")
    return {
        "强制结键": 行["强制结键"],
        "开腿键": 行["开腿键"],
        "卡号": 行["卡号"],
        "方式": 行["方式"],
        "虚出事件编号": 行["虚出事件编号"],
        "行程键": 行["行程键"],
        "操作员": 行["操作员"],
        "原因": 行["原因"],
        "处理时": 行["处理时"],
        "valid": not 问题,
        "问题": 问题,
    }


def 读强制结(开腿键: str = "") -> list:
    """强制结只读接口：开腿键、虚出或旁路、行程键、valid。"""
    conn = 连接()
    try:
        if 开腿键:
            rows = conn.execute(
                "SELECT * FROM 强制结账 WHERE 开腿键=? ORDER BY 处理时 DESC", (开腿键,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM 强制结账 ORDER BY 处理时 DESC LIMIT 200"
            ).fetchall()
        return [校一行(conn, r) for r in rows]
    finally:
        conn.close()
