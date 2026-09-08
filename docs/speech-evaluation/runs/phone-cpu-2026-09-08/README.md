# 扩展中文电话 CPU 测试

60 句独立文本、180 条 TTS 原始音频、3 种声学条件、2160 次语音识别，另有 12 次无语音控制测试。CPU：AMD64 Family 23 Model 113 Stepping 0, AuthenticAMD，CPU provider，每模型 2 线程。

**全部语音为本地合成，尚无真人电话录音。** 清晰条件也不是人工校验过的正确发音。口头填充词、自我更正来自书写文本，不能模拟真实说话人的全部韵律、口音和打断行为。

窄带条件：8kHz 抗混叠降采样、300–3400Hz 四阶 Butterworth 带通、μ-law 编解码回环。加噪条件额外叠加带限高斯噪声，按整句功率设置 12dB SNR；固定随机种子。这不代表真实蜂窝网络或运营商编解码，不含 AEC、丢包、多人重叠、真实背景音乐或 VAD。

CER 为去标点/空白与大小写标准化后的字面字符错误率；数字写法、口头语省略也可能被计为错误。关键短语全部命中仅为字符串检查，无法验证否定作用范围或业务意图。TTS 为完整短句时间，ASR 为快速回放计算 RTF，均不是端到端响应时间。

## TTS

| 模型 | 句数 | 平均完整句(s) | 中位数(s) | 加权 RTF |
|---|---:|---:|---:|---:|
| melo | 60 | 1.563 | 1.586 | 0.486 |
| matcha | 60 | 0.316 | 0.309 | 0.085 |
| kokoro_zh | 60 | 3.015 | 2.994 | 0.762 |

## ASR

| 模型 | 条件 | CER | 关键短语全部命中 | 计算 RTF |
|---|---|---:|---:|---:|
| zipformer_small | clean | 11.0% | 105/180 | 0.061 |
| zipformer_small | narrow_ulaw | 8.2% | 115/180 | 0.059 |
| zipformer_small | narrow_noise12db_ulaw | 14.6% | 82/180 | 0.059 |
| zipformer_zh | clean | 6.3% | 129/180 | 0.155 |
| zipformer_zh | narrow_ulaw | 6.3% | 129/180 | 0.152 |
| zipformer_zh | narrow_noise12db_ulaw | 11.7% | 91/180 | 0.152 |
| paraformer_streaming | clean | 5.5% | 133/180 | 0.110 |
| paraformer_streaming | narrow_ulaw | 5.8% | 127/180 | 0.108 |
| paraformer_streaming | narrow_noise12db_ulaw | 11.3% | 94/180 | 0.107 |
| sensevoice | clean | 2.9% | 153/180 | 0.060 |
| sensevoice | narrow_ulaw | 3.2% | 156/180 | 0.058 |
| sensevoice | narrow_noise12db_ulaw | 5.0% | 135/180 | 0.058 |

## 无语音控制：非空转写

| ASR | 静音 | 白噪声 | 50Hz 嗡声 |
|---|---|---|---|
| zipformer_small | （空） | （空） | （空） |
| zipformer_zh | （空） | （空） | （空） |
| paraformer_streaming | （空） | （空） | （空） |
| sensevoice | 我 | 我 | 我 |

## 分类别 CER（清晰条件）

| 分类 | Zipformer small | 中文 Zipformer | Paraformer | SenseVoice |
|---|---:|---:|---:|---:|
| 原始基线 | 7.6% | 2.5% | 2.2% | 0.8% |
| 金额与收费 | 18.2% | 8.1% | 5.4% | 3.0% |
| 否定与条件 | 8.2% | 4.5% | 5.3% | 1.9% |
| 口语更正 | 12.9% | 9.4% | 6.3% | 5.8% |
| 不确定回答 | 9.2% | 5.2% | 5.5% | 2.7% |
| 时间与地址 | 15.1% | 12.4% | 9.3% | 4.5% |
| 通话交互 | 8.0% | 4.0% | 6.1% | 2.9% |

## 关键短语未全部命中的例子（固定顺序前 20 条）

这些记录只表示字面检查未通过，数字写法差异也可能触发；完整转写保留在 JSON。

| ASR / TTS / 条件 | 参考文本 | 转写 |
|---|---|---|
| zipformer_small / melo / narrow_noise12db_ulaw | 您好，请问注册一家公司的费用是多少？ | 一家公司的费用是多少 |
| zipformer_small / melo / narrow_ulaw | 大概两千多元，具体要看公司的情况。 | 花了两千多元具体要看公司的情况 |
| zipformer_small / melo / narrow_noise12db_ulaw | 大概两千多元，具体要看公司的情况。 | 他才两千多元一起要看公司的情况 |
| zipformer_small / melo / clean | 不是三千元，是两千五百元，不包含刻章费用。 | 不是三千言是两千五百元五包含客张费用 |
| zipformer_small / melo / narrow_ulaw | 不是三千元，是两千五百元，不包含刻章费用。 | 不是三千言是两千五百元武博喊客账费用 |
| zipformer_small / melo / narrow_noise12db_ulaw | 不是三千元，是两千五百元，不包含刻章费用。 | 不是三星元寄两千五百年不含刻章费用 |
| zipformer_small / melo / narrow_noise12db_ulaw | 需要身份证和经营地址证明。 | 需要身份证和经营地执证明 |
| zipformer_small / melo / clean | 材料齐全以后，大约五个工作日可以办理完成。 | 材料齐全以后大约五贵工作日可以办理完成 |
| zipformer_small / melo / narrow_ulaw | 材料齐全以后，大约五个工作日可以办理完成。 | 还要齐全以后大约五贵工作日可以办理完成 |
| zipformer_small / melo / narrow_noise12db_ulaw | 材料齐全以后，大约五个工作日可以办理完成。 | 排到几天以后半夜五各工作日可以办理完成 |
| zipformer_small / melo / clean | 这个价格只包含注册，不包含代理记账。 | 这个价格只包含住错误包含耐力记账 |
| zipformer_small / melo / narrow_ulaw | 这个价格只包含注册，不包含代理记账。 | 这个价格只包含注册我包含耐力记账 |
| zipformer_small / melo / narrow_ulaw | 我说的是每年，不是每个月。 | 我说的是每年我是每个月 |
| zipformer_small / melo / narrow_noise12db_ulaw | 可以先咨询，但是今天不要提交申请。 | 北天咨询来是今天不要提交申请 |
| zipformer_small / melo / narrow_noise12db_ulaw | 请在九月十二日下午三点半之前联系我。 | 在九月十二月下午三点半之前联系我 |
| zipformer_small / melo / narrow_noise12db_ulaw | 银行开户需要法人到场，重庆的分公司也一样。 | 您好开户需要法人到场重庆的份工资也一样 |
| zipformer_small / melo / narrow_noise12db_ulaw | 不是不能办理，是需要补充材料以后才能办理。 | 我是不能办理是需要补充材料以后才能办理 |
| zipformer_small / melo / clean | 嗯，两千五是注册费，记账费要另外算。 | 两千五是就错费一张飞要另外算 |
| zipformer_small / melo / narrow_noise12db_ulaw | 嗯，两千五是注册费，记账费要另外算。 | 对八千五是不错费一张飞要另外算 |
| zipformer_small / melo / narrow_noise12db_ulaw | 报价是一千九百九十九，不是九百九十九。 | 我叫醒酒吧十九不是九百九十九 |

## 音频和原始数据

[本地试听页面](listen.html) · [文本快照](cases.json) · [TTS 数据](tts.json) · [ASR 数据](asr.json) · [汇总](summary.json)

保留所有 180 条清晰音频，并额外保留第 03 句的六条电话处理音频。其他处理音频按脚本和固定种子重建；所有 ASR 使用同一组预处理数组。
