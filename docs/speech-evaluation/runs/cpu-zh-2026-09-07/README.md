# 中文 CPU 语音模型对比

环境：AMD Ryzen 5 3600、Windows-11-10.0.26200-SP0、CPU provider、每模型 2 线程、单模型逐个运行；sherpa-onnx 1.13.7。每个模型预热一次，12 句各测一次。

**这不是人工录音准确率评测，也不是主观音质评分。** 36 条音频均由三个本地 TTS 生成；四个 ASR 识别完全相同的音频，分别测原采样率与 8kHz 降采样，共 288 次识别。

ASR 的 CER 为去标点/空白、NFKC、转小写后的字面编辑距离，未统一阿拉伯/中文数字。关键短语通过率为预先定义的字符串替代项检查，不等于语义正确率。8kHz 仅为抗混叠降采样，没有电话编解码、噪声、回声或丢包。

TTS 耗时是完整短句合成时间，不是流式首包；ASR 输入快速回放，RTF 是计算耗时/音频时长，不包含真实等待、VAD、LLM 或播放。

## TTS

| 模型 | 句数 | 完整句均值(s) | 中位数(s) | 范围(s) | 加权 RTF |
|---|---:|---:|---:|---:|---:|
| melo | 12 | 1.390 | 1.421 | 0.963–1.602 | 0.436 |
| matcha | 12 | 0.253 | 0.249 | 0.157–0.337 | 0.068 |
| kokoro_zh | 12 | 2.344 | 2.335 | 1.637–3.064 | 0.595 |

## ASR：合成语音一致性

| 模型 | 输入条件 | 句数 | CER | 关键短语全部命中 | 加权计算 RTF |
|---|---|---:|---:|---:|---:|
| zipformer_small | native | 36 | 7.6% | 24/36 | 0.060 |
| zipformer_small | resampled_8khz | 36 | 5.6% | 27/36 | 0.059 |
| zipformer_zh | native | 36 | 2.4% | 31/36 | 0.149 |
| zipformer_zh | resampled_8khz | 36 | 3.6% | 29/36 | 0.148 |
| paraformer_streaming | native | 36 | 2.4% | 30/36 | 0.102 |
| paraformer_streaming | resampled_8khz | 36 | 2.0% | 29/36 | 0.101 |
| sensevoice | native | 36 | 1.4% | 32/36 | 0.058 |
| sensevoice | resampled_8khz | 36 | 1.4% | 32/36 | 0.057 |

SenseVoice 是整句离线识别；其他三个使用流式模型。低 RTF 不能证明离线模型能更早返回可用文字。

## 各 TTS 音频的原采样率 CER

| ASR | MeloTTS | Matcha | Kokoro 中文 |
|---|---:|---:|---:|
| zipformer_small | 13.2% | 5.6% | 4.1% |
| zipformer_zh | 5.6% | 0.5% | 1.0% |
| paraformer_streaming | 4.1% | 1.5% | 1.5% |
| sensevoice | 3.6% | 0.5% | 0.0% |

## 试听与原始记录

[本地试听页面](listen.html)（下载目录后用浏览器打开；GitHub 文件页不直接运行 HTML）。

[TTS 原始记录](tts.json) · [ASR 原始记录](asr.json) · [汇总 JSON](summary.json)

| 句子 | MeloTTS | Matcha | Kokoro 中文 |
|---|---|---|---|
| 您好，请问注册一家公司的费用是多少？ | [试听](melo/01.wav) | [试听](matcha/01.wav) | [试听](kokoro_zh/01.wav) |
| 大概两千多元，具体要看公司的情况。 | [试听](melo/02.wav) | [试听](matcha/02.wav) | [试听](kokoro_zh/02.wav) |
| 不是三千元，是两千五百元，不包含刻章费用。 | [试听](melo/03.wav) | [试听](matcha/03.wav) | [试听](kokoro_zh/03.wav) |
| 需要身份证和经营地址证明。 | [试听](melo/04.wav) | [试听](matcha/04.wav) | [试听](kokoro_zh/04.wav) |
| 材料齐全以后，大约五个工作日可以办理完成。 | [试听](melo/05.wav) | [试听](matcha/05.wav) | [试听](kokoro_zh/05.wav) |
| 这个价格只包含注册，不包含代理记账。 | [试听](melo/06.wav) | [试听](matcha/06.wav) | [试听](kokoro_zh/06.wav) |
| 我说的是每年，不是每个月。 | [试听](melo/07.wav) | [试听](matcha/07.wav) | [试听](kokoro_zh/07.wav) |
| 如果没有实际经营地址，需要另外支付地址费用。 | [试听](melo/08.wav) | [试听](matcha/08.wav) | [试听](kokoro_zh/08.wav) |
| 可以先咨询，但是今天不要提交申请。 | [试听](melo/09.wav) | [试听](matcha/09.wav) | [试听](kokoro_zh/09.wav) |
| 请在九月十二日下午三点半之前联系我。 | [试听](melo/10.wav) | [试听](matcha/10.wav) | [试听](kokoro_zh/10.wav) |
| 银行开户需要法人到场，重庆的分公司也一样。 | [试听](melo/11.wav) | [试听](matcha/11.wav) | [试听](kokoro_zh/11.wav) |
| 不是不能办理，是需要补充材料以后才能办理。 | [试听](melo/12.wav) | [试听](matcha/12.wav) | [试听](kokoro_zh/12.wav) |
