import React, { useEffect, useState } from "react";
import { 取, 递 } from "../api.js";

export default function 开腿账() {
  const [行, 设] = useState([]);
  const [结, 设结] = useState([]);
  const [讯, 设讯] = useState("");
  const 刷 = () => {
    取("/开腿账").then(设);
    取("/强制结").then(设结);
  };
  useEffect(() => { 刷(); }, []);
  const 强制结 = async (开腿键) => {
    设讯("");
    try {
      const r = await 递("/强制结", { 开腿键, 原因: "闭站强制结" });
      设讯(`已强制结 ${r.开腿键}：虚出 ${r.虚出事件编号} → 行程 ${r.行程键}${r.幂等 ? "（幂等重放）" : ""}`);
    } catch (e) {
      设讯(`强制结被拒：${e.message}`);
    }
    await 刷();
  };
  return (
    <div>
      <h1 className="题">开腿账</h1>
      <p className="述">
        进站尚未出站的在途开腿。出站成对变已成对；超时或重复进站变已旁路；
        闭站可对在途腿点「强制结」——口径=虚出成对：同事务写虚出刷卡+行程，其后真实出站互斥进旁路。
      </p>
      <div className="行"><button className="次" onClick={刷}>刷新</button></div>
      {讯 && <div className="行"><span className="讯" style={{ margin: 0 }}>{讯}</span></div>}
      {!行.length ? <div className="空态">暂无开腿。</div> : (
        <table>
          <thead><tr><th>开腿键</th><th>卡号</th><th>进站站</th><th>进站时</th><th>状态</th><th>写闸</th></tr></thead>
          <tbody>
            {行.map((r) => (
              <tr key={r.开腿键}>
                <td>{r.开腿键}</td><td>{r.卡号}</td><td>{r.进站站点键}</td>
                <td>{r.进站事件时}</td><td>{r.状态}</td>
                <td>{r.状态 === "在途"
                  ? <button className="次" onClick={() => 强制结(r.开腿键)}>强制结</button>
                  : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h2 className="题" style={{ fontSize: "1.05rem", marginTop: "1.4rem" }}>强制结记录（只读）</h2>
      {!结.length ? <div className="空态">尚无强制结。</div> : (
        <table>
          <thead><tr><th>开腿键</th><th>卡号</th><th>方式</th><th>虚出事件</th><th>行程键</th><th>valid</th><th>处理时</th></tr></thead>
          <tbody>
            {结.map((j) => (
              <tr key={j.强制结键}>
                <td>{j.开腿键}</td><td>{j.卡号}</td><td>{j.方式}</td>
                <td>{j.虚出事件编号}</td><td>{j.行程键}</td>
                <td><span className={`徽章 ${j.valid ? "通" : "败"}`}>{j.valid ? "valid" : "invalid"}</span></td>
                <td>{j.处理时}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
