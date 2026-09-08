"""Summarize the expanded phone corpus, preserving raw observations."""

import argparse
import html
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="phone-cpu-2026-09-08")
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in {".", ".."}:
        raise ValueError("Invalid run name")
    OUT = ROOT / "runs" / args.run
    t = json.loads((OUT / "tts.json").read_text(encoding="utf-8"))
    a = json.loads((OUT / "asr.json").read_text(encoding="utf-8"))
    cases = json.loads((OUT / "cases.json").read_text(encoding="utf-8"))
    expected = len(cases) * 3 * 3 * 4
    assert len(t["rows"]) == len(cases) * 3 and len(a["rows"]) == expected
    assert len(a["controls"]) == 12
    lines = [
        "# 扩展中文电话 CPU 测试",
        "",
        f"{len(cases)} 句独立文本、{len(t['rows'])} 条 TTS 原始音频、3 种声学条件、"
        f"{expected} 次语音识别，另有 12 次无语音控制测试。"
        f"CPU：{t['meta']['cpu']}，CPU provider，每模型 2 线程。",
        "",
        "**全部语音为本地合成，尚无真人电话录音。** 清晰条件也不是人工校验过的正确发音。"
        "口头填充词、自我更正来自书写文本，不能模拟真实说话人的全部韵律、口音和打断行为。",
        "",
        "窄带条件：8kHz 抗混叠降采样、300–3400Hz 四阶 Butterworth 带通、μ-law 编解码回环。"
        "加噪条件额外叠加带限高斯噪声，按整句功率设置 12dB SNR；固定随机种子。"
        "这不代表真实蜂窝网络或运营商编解码，不含 AEC、丢包、多人重叠、真实背景音乐或 VAD。",
        "",
        "CER 为去标点/空白与大小写标准化后的字面字符错误率；数字写法、口头语省略也可能被计为错误。"
        "关键短语全部命中仅为字符串检查，无法验证否定作用范围或业务意图。"
        "TTS 为完整短句时间，ASR 为快速回放计算 RTF，均不是端到端响应时间。",
        "",
        "## TTS",
        "",
        "| 模型 | 句数 | 平均完整句(s) | 中位数(s) | 加权 RTF |",
        "|---|---:|---:|---:|---:|",
    ]
    summary = dict(tts=[], asr=[])
    for name in ["melo", "matcha", "kokoro_zh"]:
        rows = [r for r in t["rows"] if r["tts"] == name]
        times = [r["full_sentence_s"] for r in rows]
        rtf = sum(times) / sum(r["audio_s"] for r in rows)
        summary["tts"].append(dict(name=name, mean_s=statistics.mean(times), rtf=rtf))
        lines.append(
            f"| {name} | {len(rows)} | {statistics.mean(times):.3f} | "
            f"{statistics.median(times):.3f} | {rtf:.3f} |"
        )
    lines += [
        "",
        "## ASR",
        "",
        "| 模型 | 条件 | CER | 关键短语全部命中 | 计算 RTF |",
        "|---|---|---:|---:|---:|",
    ]
    for name in ["zipformer_small", "zipformer_zh", "paraformer_streaming", "sensevoice"]:
        for cond in ["clean", "narrow_ulaw", "narrow_noise12db_ulaw"]:
            rows = [r for r in a["rows"] if r["asr"] == name and r["condition"] == cond]
            cer = sum(r["edits"] for r in rows) / sum(r["reference_chars"] for r in rows)
            good = sum(r["all_keyphrases"] for r in rows)
            rtf = sum(r["compute_s"] for r in rows) / sum(r["audio_s"] for r in rows)
            summary["asr"].append(
                dict(
                    name=name,
                    condition=cond,
                    cer=cer,
                    all_keyphrases=good,
                    total=len(rows),
                    rtf=rtf,
                )
            )
            lines.append(f"| {name} | {cond} | {cer:.1%} | {good}/{len(rows)} | {rtf:.3f} |")
    lines += [
        "",
        "## 无语音控制：非空转写",
        "",
        "| ASR | 静音 | 白噪声 | 50Hz 嗡声 |",
        "|---|---|---|---|",
    ]
    for name in ["zipformer_small", "zipformer_zh", "paraformer_streaming", "sensevoice"]:
        rows = [r for r in a["controls"] if r["asr"] == name]
        lines.append("| " + name + " | " + " | ".join(r["text"] or "（空）" for r in rows) + " |")
    lines += [
        "",
        "## 分类别 CER（清晰条件）",
        "",
        "| 分类 | Zipformer small | 中文 Zipformer | Paraformer | SenseVoice |",
        "|---|---:|---:|---:|---:|",
    ]
    labels = {
        "baseline": "原始基线",
        "money": "金额与收费",
        "negation": "否定与条件",
        "correction": "口语更正",
        "uncertainty": "不确定回答",
        "time_address": "时间与地址",
        "interaction": "通话交互",
    }
    for category in dict.fromkeys(c["category"] for c in cases):
        cells = []
        for name in ["zipformer_small", "zipformer_zh", "paraformer_streaming", "sensevoice"]:
            rows = [
                r
                for r in a["rows"]
                if r["asr"] == name and r["condition"] == "clean" and r["category"] == category
            ]
            cells.append(
                f"{sum(r['edits'] for r in rows) / sum(r['reference_chars'] for r in rows):.1%}"
            )
        lines.append("| " + labels.get(category, category) + " | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 关键短语未全部命中的例子（固定顺序前 20 条）",
        "",
        "这些记录只表示字面检查未通过，数字写法差异也可能触发；完整转写保留在 JSON。",
        "",
        "| ASR / TTS / 条件 | 参考文本 | 转写 |",
        "|---|---|---|",
    ]
    failures = [r for r in a["rows"] if not r["all_keyphrases"]][:20]
    for row in failures:
        lines.append(
            f"| {row['asr']} / {row['tts']} / {row['condition']} | "
            f"{row['reference'].replace('|', '／')} | {row['text'].replace('|', '／')} |"
        )
    lines += [
        "",
        "## 音频和原始数据",
        "",
        "[本地试听页面](listen.html) · [文本快照](cases.json) · [TTS 数据](tts.json) · "
        "[ASR 数据](asr.json) · [汇总](summary.json)",
        "",
        "保留所有 180 条清晰音频，并额外保留第 03 句的六条电话处理音频。"
        "其他处理音频按脚本和固定种子重建；所有 ASR 使用同一组预处理数组。",
    ]
    page = [
        '<!doctype html><meta charset="utf-8"><title>扩展电话测试试听</title>',
        "<style>body{font:16px system-ui;margin:24px}td,th{padding:10px;border:1px solid #ddd}"
        "table{border-collapse:collapse}audio{width:240px}</style>",
        "<h1>60 句中文电话场景：三个 TTS 对比</h1><p>全部为合成音频。请听辨漏字、"
        "否定、金额、重音和多音字；没有自动主观音质评分。</p>",
        "<table><tr><th>分类与文本</th><th>MeloTTS</th><th>Matcha</th><th>Kokoro 中文</th></tr>",
    ]
    for c in cases:
        page.append(
            "<tr><td>"
            + html.escape(c["category"] + " / " + c["text"])
            + "</td>"
            + "".join(
                f'<td><audio controls preload="none" src="{n}/{c["id"]}.wav"></audio></td>'
                for n in ["melo", "matcha", "kokoro_zh"]
            )
            + "</tr>"
        )
    page.append("</table><h2>第 03 句：模拟电话条件</h2>")
    for n in ["melo", "matcha", "kokoro_zh"]:
        for cond in ["narrow_ulaw", "narrow_noise12db_ulaw"]:
            page.append(
                f'<p>{n} / {cond}</p><audio controls preload="none" '
                f'src="{n}/03-{cond}.wav"></audio>'
            )
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "listen.html").write_text("\n".join(page), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
