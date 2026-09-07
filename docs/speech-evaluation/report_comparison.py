"""Render auditable tables and a local listening page from comparison records."""

import argparse
import html
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="cpu-zh-2026-09-07")
    args = parser.parse_args()
    folder = ROOT / "runs" / args.run
    tts = json.loads((folder / "tts.json").read_text(encoding="utf-8"))
    asr = json.loads((folder / "asr.json").read_text(encoding="utf-8"))
    assert len(tts["rows"]) == 36
    assert len(asr["rows"]) == 288
    lines = [
        "# 中文 CPU 语音模型对比",
        "",
        f"环境：{tts['meta']['cpu']}、{tts['meta']['platform']}、CPU provider、"
        f"每模型 {tts['meta']['threads']} 线程、单模型逐个运行；"
        "sherpa-onnx " + tts["meta"]["runtime"] + "。每个模型预热一次，12 句各测一次。",
        "",
        "**这不是人工录音准确率评测，也不是主观音质评分。** "
        "36 条音频均由三个本地 TTS 生成；四个 ASR 识别完全相同的音频，"
        "分别测原采样率与 8kHz 降采样，共 288 次识别。",
        "",
        "ASR 的 CER 为去标点/空白、NFKC、转小写后的字面编辑距离，未统一阿拉伯/中文数字。"
        "关键短语通过率为预先定义的字符串替代项检查，不等于语义正确率。"
        "8kHz 仅为抗混叠降采样，没有电话编解码、噪声、回声或丢包。",
        "",
        "TTS 耗时是完整短句合成时间，不是流式首包；ASR 输入快速回放，RTF 是计算耗时/音频时长，"
        "不包含真实等待、VAD、LLM 或播放。",
        "",
        "## TTS",
        "",
        "| 模型 | 句数 | 完整句均值(s) | 中位数(s) | 范围(s) | 加权 RTF |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    summary = dict(tts=[], asr=[])
    for model in tts["models"]:
        name = model["name"]
        rows = [r for r in tts["rows"] if r["tts"] == name]
        times = [r["full_sentence_s"] for r in rows]
        rtf = sum(times) / sum(r["audio_s"] for r in rows)
        summary["tts"].append(
            dict(
                name=name, mean_s=statistics.mean(times), median_s=statistics.median(times), rtf=rtf
            )
        )
        lines.append(
            f"| {name} | {len(rows)} | {statistics.mean(times):.3f} | "
            f"{statistics.median(times):.3f} | {min(times):.3f}–{max(times):.3f} | {rtf:.3f} |"
        )
    lines += [
        "",
        "## ASR：合成语音一致性",
        "",
        "| 模型 | 输入条件 | 句数 | CER | 关键短语全部命中 | 加权计算 RTF |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for model in asr["models"]:
        for condition in ["native", "resampled_8khz"]:
            rows = [
                r for r in asr["rows"] if r["asr"] == model["name"] and r["condition"] == condition
            ]
            cer = sum(r["edits"] for r in rows) / sum(r["reference_chars"] for r in rows)
            rtf = sum(r["compute_s"] for r in rows) / sum(r["audio_s"] for r in rows)
            good = sum(r["all_keyphrases"] for r in rows)
            summary["asr"].append(
                dict(
                    name=model["name"],
                    condition=condition,
                    cer=cer,
                    rtf=rtf,
                    all_keyphrases=good,
                    total=len(rows),
                )
            )
            lines.append(
                f"| {model['name']} | {condition} | {len(rows)} | {cer:.1%} | "
                f"{good}/{len(rows)} | {rtf:.3f} |"
            )
    lines += [
        "",
        "SenseVoice 是整句离线识别；其他三个使用流式模型。"
        "低 RTF 不能证明离线模型能更早返回可用文字。",
        "",
        "## 各 TTS 音频的原采样率 CER",
        "",
        "| ASR | MeloTTS | Matcha | Kokoro 中文 |",
        "|---|---:|---:|---:|",
    ]
    for model in asr["models"]:
        values = []
        for t in ["melo", "matcha", "kokoro_zh"]:
            rows = [
                r
                for r in asr["rows"]
                if r["asr"] == model["name"] and r["tts"] == t and r["condition"] == "native"
            ]
            values.append(sum(r["edits"] for r in rows) / sum(r["reference_chars"] for r in rows))
        lines.append("| " + model["name"] + " | " + " | ".join(f"{v:.1%}" for v in values) + " |")
    lines += [
        "",
        "## 试听与原始记录",
        "",
        "[本地试听页面](listen.html)（下载目录后用浏览器打开；GitHub 文件页不直接运行 HTML）。",
        "",
        "[TTS 原始记录](tts.json) · [ASR 原始记录](asr.json) · [汇总 JSON](summary.json)",
        "",
        "| 句子 | MeloTTS | Matcha | Kokoro 中文 |",
        "|---|---|---|---|",
    ]
    page = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        "<title>中文 CPU TTS 试听</title><style>body{font:16px system-ui;max-width:1200px;"
        "margin:30px auto;padding:0 20px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ddd;padding:12px;text-align:left}audio{width:250px}"
        "</style><h1>中文 CPU TTS 对比试听</h1><p>同一句文本的三种合成结果。"
        "请检查漏字、多音字、否定、金额、自然度和停顿。没有自动主观音质评分。</p>"
        "<table><tr><th>文本</th><th>MeloTTS</th><th>Matcha</th><th>Kokoro v1.1 中文</th></tr>",
    ]
    cases = json.loads((ROOT / "chinese-cases.json").read_text(encoding="utf-8"))
    for case in cases:
        links = [f"{t}/{case['id']}.wav" for t in ["melo", "matcha", "kokoro_zh"]]
        lines.append("| " + case["text"] + " | " + " | ".join(f"[试听]({p})" for p in links) + " |")
        page.append(
            "<tr><td>"
            + html.escape(case["text"])
            + "</td>"
            + "".join(f'<td><audio controls preload="none" src="{p}"></audio></td>' for p in links)
            + "</tr>"
        )
    page.append("</table></html>")
    (folder / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "listen.html").write_text("\n".join(page), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
