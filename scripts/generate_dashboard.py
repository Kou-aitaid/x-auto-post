"""
HTMLダッシュボード生成スクリプト
publish_log.json / analytics_insights.json を元にダッシュボードを生成して
docs/index.html に保存する
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict

DATA_DIR  = Path(__file__).parent.parent / "data"
DOCS_DIR  = Path(__file__).parent.parent / "docs"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def calc_eng(like: int, rt: int, reply: int) -> float:
    return round((like + rt * 2 + reply * 3) / 100, 2)


def build_stats(logs: List[Dict]) -> Dict:
    post_logs = [l for l in logs if l.get("type") != "followers"]
    follower_logs = sorted(
        [l for l in logs if l.get("type") == "followers"],
        key=lambda x: x.get("recorded_at", "")
    )

    # タイプ別集計
    by_type: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "likes": 0, "rts": 0, "replies": 0})
    for l in post_logs:
        t = l.get("type", "不明")
        by_type[t]["count"] += 1
        by_type[t]["likes"]   += l.get("likes", 0) or 0
        by_type[t]["rts"]     += l.get("retweets", 0) or 0
        by_type[t]["replies"] += l.get("replies", 0) or 0

    # 週別投稿数（直近8週）
    by_week: Dict[str, int] = defaultdict(int)
    for l in post_logs:
        date_str = l.get("scheduled_date", l.get("approved_at", ""))[:10]
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str)
                week = dt.strftime("%m/%d週")
                by_week[week] += 1
            except Exception:
                pass
    week_labels = sorted(by_week.keys())[-8:]
    week_data   = [by_week[w] for w in week_labels]

    # 直近10投稿
    recent = [l for l in post_logs if l.get("likes") is not None][-10:]

    return {
        "post_count": len(post_logs),
        "total_likes": sum((l.get("likes", 0) or 0) for l in post_logs),
        "total_rts":   sum((l.get("retweets", 0) or 0) for l in post_logs),
        "by_type": dict(by_type),
        "week_labels": week_labels,
        "week_data":   week_data,
        "followers_latest": follower_logs[-1]["count"] if follower_logs else None,
        "followers_prev":   follower_logs[-2]["count"] if len(follower_logs) >= 2 else None,
        "recent": recent,
    }


def render_html(stats: Dict, insights: Dict) -> str:
    updated = datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M UTC")
    report  = insights.get("latest_report", "データ蓄積中...") if insights else "データ蓄積中..."
    actions = insights.get("action_items", []) if insights else []

    # タイプ別棒グラフ用データ
    type_labels = list(stats["by_type"].keys())
    type_likes  = [stats["by_type"][t]["likes"] for t in type_labels]
    type_rts    = [stats["by_type"][t]["rts"]   for t in type_labels]

    # フォロワー増減
    fw_latest = stats["followers_latest"]
    fw_prev   = stats["followers_prev"]
    fw_diff   = f"+{fw_latest - fw_prev}" if fw_latest and fw_prev else "—"
    fw_disp   = f"{fw_latest:,}" if fw_latest else "—"

    # 直近投稿テーブル
    recent_rows = ""
    for l in reversed(stats["recent"]):
        content = (l.get("content") or "")[:40] + "..."
        likes   = l.get("likes", "—")
        rts     = l.get("retweets", "—")
        replies = l.get("replies", "—")
        ptype   = l.get("type", "")
        date    = (l.get("scheduled_date") or l.get("approved_at", ""))[:10]
        recent_rows += f"""
        <tr>
          <td>{date}</td>
          <td><span class="badge">{ptype}</span></td>
          <td class="content">{content}</td>
          <td class="num">{likes}</td>
          <td class="num">{rts}</td>
          <td class="num">{replies}</td>
        </tr>"""

    # 改善アクション
    action_html = "".join(f"<li>{a}</li>" for a in actions) if actions else "<li>データ蓄積中...</li>"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>@kansai_job_ ダッシュボード</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Hiragino Sans',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh;padding:24px}}
  h1{{font-size:1.4rem;color:#7dd3fc;margin-bottom:4px}}
  .sub{{font-size:.8rem;color:#64748b;margin-bottom:24px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:24px}}
  .card{{background:#1e2330;border-radius:12px;padding:20px;border:1px solid #2d3748}}
  .card .label{{font-size:.75rem;color:#64748b;margin-bottom:6px}}
  .card .value{{font-size:1.8rem;font-weight:700;color:#f1f5f9}}
  .card .diff{{font-size:.85rem;color:#34d399;margin-top:4px}}
  .section{{background:#1e2330;border-radius:12px;padding:20px;margin-bottom:20px;border:1px solid #2d3748}}
  .section h2{{font-size:1rem;color:#94a3b8;margin-bottom:16px;border-bottom:1px solid #2d3748;padding-bottom:8px}}
  .chart-wrap{{position:relative;height:220px}}
  table{{width:100%;border-collapse:collapse;font-size:.85rem}}
  th{{text-align:left;color:#64748b;font-weight:500;padding:8px 12px;border-bottom:1px solid #2d3748}}
  td{{padding:8px 12px;border-bottom:1px solid #1a202c}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  td.content{{color:#cbd5e1;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .badge{{background:#1e3a5f;color:#7dd3fc;border-radius:4px;padding:2px 6px;font-size:.75rem}}
  .report{{background:#162032;border-left:3px solid #3b82f6;padding:12px 16px;border-radius:0 8px 8px 0;font-size:.9rem;line-height:1.7;color:#cbd5e1;margin-bottom:16px}}
  ul.actions{{list-style:none;display:flex;flex-direction:column;gap:8px}}
  ul.actions li::before{{content:"▶ ";color:#3b82f6}}
  ul.actions li{{font-size:.9rem;color:#cbd5e1}}
  .footer{{text-align:center;font-size:.75rem;color:#374151;margin-top:32px}}
</style>
</head>
<body>
<h1>📊 @kansai_job_ パフォーマンスダッシュボード</h1>
<p class="sub">最終更新: {updated}（週次自動更新）</p>

<!-- KPIカード -->
<div class="grid">
  <div class="card">
    <div class="label">フォロワー数</div>
    <div class="value">{fw_disp}</div>
    <div class="diff">今週の増減 {fw_diff}</div>
  </div>
  <div class="card">
    <div class="label">総投稿数</div>
    <div class="value">{stats["post_count"]}</div>
  </div>
  <div class="card">
    <div class="label">総いいね数</div>
    <div class="value">{stats["total_likes"]:,}</div>
  </div>
  <div class="card">
    <div class="label">総RT数</div>
    <div class="value">{stats["total_rts"]:,}</div>
  </div>
</div>

<!-- 投稿タイプ別グラフ -->
<div class="section">
  <h2>投稿タイプ別 いいね・RT数</h2>
  <div class="chart-wrap">
    <canvas id="typeChart"></canvas>
  </div>
</div>

<!-- 週別投稿数 -->
<div class="section">
  <h2>週別投稿数</h2>
  <div class="chart-wrap">
    <canvas id="weekChart"></canvas>
  </div>
</div>

<!-- 直近投稿 -->
<div class="section">
  <h2>直近の投稿パフォーマンス（Nitter自動取得）</h2>
  <table>
    <thead>
      <tr><th>日付</th><th>タイプ</th><th>投稿内容</th><th>いいね</th><th>RT</th><th>返信</th></tr>
    </thead>
    <tbody>{recent_rows or '<tr><td colspan="6" style="text-align:center;color:#64748b">データ蓄積中...</td></tr>'}</tbody>
  </table>
</div>

<!-- AI分析レポート -->
<div class="section">
  <h2>AI 週次レポート</h2>
  <div class="report">{report}</div>
  <h2 style="margin-top:16px">今週やること</h2>
  <ul class="actions">{action_html}</ul>
</div>

<p class="footer">GitHub Actions × Gemini API × Nitter で完全自動生成</p>

<script>
const typeLabels = {json.dumps(type_labels, ensure_ascii=False)};
const typeLikes  = {json.dumps(type_likes)};
const typeRTs    = {json.dumps(type_rts)};

new Chart(document.getElementById('typeChart'), {{
  type: 'bar',
  data: {{
    labels: typeLabels,
    datasets: [
      {{label:'いいね', data:typeLikes, backgroundColor:'rgba(251,191,36,.8)'}},
      {{label:'RT',     data:typeRTs,   backgroundColor:'rgba(59,130,246,.8)'}}
    ]
  }},
  options: {{
    responsive:true, maintainAspectRatio:false,
    plugins:{{legend:{{labels:{{color:'#94a3b8'}}}}}},
    scales:{{x:{{ticks:{{color:'#64748b'}},grid:{{color:'#2d3748'}}}},
             y:{{ticks:{{color:'#64748b'}},grid:{{color:'#2d3748'}}}}}}
  }}
}});

new Chart(document.getElementById('weekChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(stats["week_labels"])},
    datasets: [{{
      label:'投稿数',
      data: {json.dumps(stats["week_data"])},
      borderColor:'#7dd3fc', backgroundColor:'rgba(125,211,252,.15)',
      tension:0.3, fill:true
    }}]
  }},
  options: {{
    responsive:true, maintainAspectRatio:false,
    plugins:{{legend:{{labels:{{color:'#94a3b8'}}}}}},
    scales:{{x:{{ticks:{{color:'#64748b'}},grid:{{color:'#2d3748'}}}},
             y:{{ticks:{{color:'#64748b',stepSize:1}},grid:{{color:'#2d3748'}}}}}}
  }}
}});
</script>
</body>
</html>"""


def main():
    print("=== ダッシュボード生成開始 ===")

    logs     = load_json(DATA_DIR / "publish_log.json", [])
    insights = load_json(DATA_DIR / "analytics_insights.json", {})

    stats = build_stats(logs)
    html  = render_html(stats, insights)

    DOCS_DIR.mkdir(exist_ok=True)
    out = DOCS_DIR / "index.html"
    out.write_text(html, encoding="utf-8")

    print(f"  → {out} を生成しました")
    print(f"  投稿数: {stats['post_count']} / フォロワー: {stats['followers_latest']}")
    print("=== 完了 ===")


if __name__ == "__main__":
    main()
