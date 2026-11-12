# 语音输入与朗读输出：设计规格

**日期**：2026-11-12
**状态**：待实现
**范围**：为工业手册问答工作台增加语音输入（语音转文字）与朗读回答（文字转语音）

---

## 1. 目标与非目标

### 目标

现场维护人员戴手套或双手不便时，可用语音提问；也可让系统朗读答案，避免低头看屏幕。

1. **语音输入**：录音 → 转文字 → 填入输入框（用户可校对后再发送）
2. **朗读回答**：点击朗读 → 后台合成 → 浏览器播放

### 非目标（本期明确不做）

- **全语音对话**（说话即自动发送）：工业术语识别错字代价高，必须保留人工校对环节
- **流式实时识别**：需 WebSocket 双工通道，与现有请求-响应架构差异过大
- **逐答案点选择朗读**：实测回答中位 82 字符，整段朗读足够
- **对话历史朗读**：仅朗读当前这条回答

---

## 2. 技术事实（已实测，非假设）

| 项 | 结论 | 验证方式 |
| --- | --- | --- |
| ASR 模型 | `qwen3-asr-flash` | 实测返回「泵的轴承温度上限是八十五摄氏度。」 |
| ASR 端点 | `POST /api/v1/services/aigc/multimodal-generation/generation` | 同上 |
| ASR 输入 | `input.messages[0].content[0] = {type:"audio", audio:"data:<mime>;base64,<data>"}` | 实测通过 |
| TTS 模型 | `qwen3-tts-flash`，音色 `Cherry` | 实测返回 652KB 音频 |
| TTS 输出 | `output.audio.url`（24 小时有效）或流式 `output.audio.data`（base64） | 实测通过 |
| MIME 声明 | **被服务端忽略**，按内容探测 | 同一 wav 字节声明为 `audio/webm` 仍识别成功 |
| TTS 文本上限 | 600 字符 | 官方文档 |
| 实际回答长度 | 历史 200 条：中位 82、均值 103、最大 424 | 本地 `answer_feedback` 统计 |
| 凭证 | 复用现有 `DASHSCOPE_API_KEY` | 无需新增密钥 |

**结论**：无需引入前端音频转码层。浏览器 `MediaRecorder` 原生输出的 `audio/webm;codecs=opus` 可直接上传。

---

## 3. 已确认的产品决策

| 决策项 | 选择 | 理由 |
| --- | --- | --- |
| 功能范围 | 语音输入 + 朗读回答 | 用户选择 |
| 传输方式 | 上传音频文件 | 与现有 `UploadFile` 模式一致 |
| 朗读实现 | 后端合成返回音频流 | 不暴露 Key，可限流可审计 |
| 鉴权边界 | 匿名公开 | 与现有 `POST /query` 一致的安全模型 |
| 限流 | 10 次/分钟（`BUCKET_VOICE`） | 语音成本高于文本 query |
| 默认开关 | `VOICE_ENABLED=false` | 烧钱接口，需显式开启 |
| 录音上限 | 60 秒 / 5MB | 覆盖工业现场单句提问 |
| 朗读范围 | 仅答案正文 | 不读引用标记与依据抽屉 |
| 音频留存 | 不落盘 | 避免客户环境录音的合规风险 |
| 验收覆盖 | 不纳入问答题库 | 题库测答案质量，语音是通道 |

---

## 4. 架构

```
浏览器                          后端 FastAPI                        DashScope
  │                                │                                  │
  ├─ MediaRecorder 录音            │                                  │
  │  audio/webm;codecs=opus       │                                  │
  ├──POST /v1/voice/transcribe────►│                                  │
  │   multipart: file, duration    │                                  │
  │                                ├─ 1. VOICE_ENABLED 检查            │
  │                                ├─ 2. 匿名限流 BUCKET_VOICE         │
  │                                ├─ 3. 大小/时长/文件头校验           │
  │                                ├─ 4. 调 ASR ─────────────────────►│ qwen3-asr-flash
  │                                │◄──── text, language, emotion ────┤
  │                                ├─ 5. 低置信度判定                  │
  │◄── {text, language, low_confidence} ─┤                            │
  │  填入输入框（可校对，不自动发送）  │                                  │
  │                                │                                  │
  ├──POST /v1/voice/speak─────────►│                                  │
  │   {text, request_id?}          │                                  │
  │                                ├─ 1. 开关 + 限流 + 文本长度        │
  │                                ├─ 2. 清洗文本（剥引用、单位转口语） │
  │                                ├─ 3. 调 TTS ─────────────────────►│ qwen3-tts-flash
  │                                │◄──── audio url / data ───────────┤
  │                                ├─ 4. 下载音频并转流                │
  │◄── audio/wav 流 ───────────────┤                                  │
  │  <audio> 播放                   │                                  │
```

### 为什么后端代理而非前端直连

1. `DASHSCOPE_API_KEY` 不能进入浏览器
2. 复用现有 `AnonymousRateLimiter`（语音是烧钱接口）
3. 可写入 `retrieval_traces` 做审计
4. 可在服务端做文件头校验与文本清洗

---

## 5. 组件设计

每个单元职责单一、可独立测试。

### 5.1 `src/industrial_rag/voice_text.py` — 纯函数，无 IO

| 函数 | 职责 |
| --- | --- |
| `clean_for_speech(text: str, *, max_chars: int = 600) -> str` | 剥离 `[1]` 引用标记、去除 Markdown、`°C`→`摄氏度`、`mm`→`毫米` 等、超长截断 |
| `is_low_confidence(text: str, *, language: str \| None) -> bool` | 识别结果判定：去空白后长度 ≤ 2 视为低置信 |
| `sniff_audio_kind(data: bytes) -> str \| None` | 文件头探测：`RIFF`→wav，`1A45DFA3`→webm，其余→`None` |

**为什么独立成模块**：这三条都是确定性规则，必须能单测，不能藏在路由里。

### 5.2 `src/industrial_rag/voice_provider.py` — 可替换适配器

```python
class VoiceProvider(Protocol):
    async def transcribe(self, audio: bytes, mime: str) -> TranscriptionResult: ...
    async def synthesize(self, text: str, *, voice: str) -> bytes: ...

class DashScopeVoiceProvider:
    """Real implementation: httpx to multimodal-generation endpoint."""
```

`TranscriptionResult` 含 `text`、`language`、`emotion`、`raw`。

**为什么抽接口**：测试用假 provider，不消耗额度、不依赖网络。

### 5.3 `src/industrial_rag/routers/voice.py` — 两个端点

| 端点 | 方法 | 输入 | 输出 |
| --- | --- | --- | --- |
| `/v1/voice/transcribe` | POST | `multipart/form-data`：`file`、`duration_ms` | `{text, language, low_confidence}` |
| `/v1/voice/speak` | POST | JSON：`{text}` | `audio/wav` 流 |

### 5.4 前端

| 文件 | 职责 |
| --- | --- |
| `composables/useVoiceRecorder.ts` | 录音状态机：`idle → requesting → recording → uploading → done/error` |
| `composables/useSpeech.ts` | 播放状态机：`idle → synthesizing → playing → done/error` |
| `views/ChatView.vue` | 麦克风按钮（输入框旁）+ 朗读按钮（回答下方） |

---

## 6. 既有约束（实现时必须处理）

### 6.1 限流桶当前是二元硬编码

`rate_limit.py:76` 现状：

```python
return self._feedback_limit if bucket == BUCKET_FEEDBACK else self._default_limit
```

新增 `BUCKET_VOICE` 需改为字典映射：

```python
def limit_for(self, bucket: str = BUCKET_DEFAULT) -> int:
    return self._limits.get(bucket, self._default_limit)
```

**这是修改既有代码，不是纯新增。** 该函数被 8 个测试覆盖，必须先补测试再改。

### 6.2 公开路由契约白名单

`tests/test_public_route_contract.py` 的 `PUBLIC_CASES` 必须显式登记两个新端点，并在 `PRIVATE_CASES` 增加近似反例：

- `POST /v1/voice/transcribe/extra`（不该公开）
- `POST /v1/voice`（不该公开）
- `GET /v1/voice/speak`（方法不匹配）

### 6.3 ASR 对无效音频会幻觉出文字（实测缺陷）

传入 4004 字节随机数据 + WebM 魔数，返回 `{"text": "啊。", "emotion": "angry"}` 而非报错。

**三道防线**：

1. **前端**：录音时长 < 500ms 不上传，提示「说话时间太短」
2. **后端**：`sniff_audio_kind()` 校验文件头，不匹配返回 415 `VOICE_AUDIO_UNRECOGNISED`
3. **结果**：`is_low_confidence()` 判定（去空白长度 ≤ 2），返回 `low_confidence: true`，前端提示「未识别清楚，请重试」且**不填入输入框**

第 3 条最关键：前两条挡不住「结构合法但内容是噪声」的情况。

---

## 7. 错误处理

沿用现有 `AppError` 约定。

| 场景 | HTTP | code | 前端行为 |
| --- | --- | --- | --- |
| 语音功能未开启 | 404 | `VOICE_DISABLED` | 按钮不渲染（前端读 `/v1/config` 或首次请求 404 后隐藏） |
| 超限流 | 429 | `RATE_LIMITED` | 提示等待 `Retry-After` 秒 |
| 音频超 5MB / 60 秒 | 413 | `VOICE_AUDIO_TOO_LARGE` | 提示「录音过长」 |
| 文件头无法识别 | 415 | `VOICE_AUDIO_UNRECOGNISED` | 提示「录音失败，请重试」 |
| ASR 返回空文字 | 200 | — | 提示「未识别到语音」 |
| ASR 返回低置信 | 200 | — | 提示「未识别清楚」，`low_confidence: true` |
| TTS 文本超 600 | 200 | — | 后端截断，响应头 `X-Voice-Truncated: true` 标注 |
| 上游 429 / 欠费 | 503 | `VOICE_UPSTREAM_UNAVAILABLE` | 提示稍后重试 |
| 麦克风权限拒绝 | — | — | 前端捕获 `NotAllowedError`，不调后端 |

---

## 8. 配置项

新增到 `config.py` 与 `.env.docker.example`：

```
VOICE_ENABLED=false                      # 默认关闭，需显式开启
VOICE_ASR_MODEL=qwen3-asr-flash
VOICE_TTS_MODEL=qwen3-tts-flash
VOICE_TTS_VOICE=Cherry
VOICE_MAX_AUDIO_BYTES=5242880            # 5MB
VOICE_MAX_AUDIO_SECONDS=60
VOICE_TTS_MAX_CHARS=600
VOICE_RATE_LIMIT_PER_MINUTE=10
```

---

## 9. 安全与合规

| 项 | 处理 |
| --- | --- |
| API Key | 仅在后端环境变量，永不进入浏览器 |
| 音频留存 | **不落盘**，仅在请求内存中中转，请求结束即释放 |
| 审计 | 记录 `request_id`、时长、字符数、结果长度；**不记录音频内容与识别文本原文** |
| 限流 | `BUCKET_VOICE` 10 次/分钟，防止额度滥用 |
| 日志 | 识别文本不写 INFO 日志（含现场信息），仅记录长度 |

---

## 10. 测试策略

### 单元测试（必须）

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_voice_text.py` | 引用剥离、单位转换、超长截断、空输入、低置信判定、文件头探测（含反例） |
| `tests/test_voice_provider.py` | 假 provider：成功、超时、上游错误、畸形响应、空结果 |
| `tests/test_voice_router.py` | TestClient + 假 provider：开关关闭 404、限流 429、超大 413、坏头 415、低置信、上游失败 503 |
| `tests/test_public_route_contract.py` | 补 `PUBLIC_CASES` 两项 + `PRIVATE_CASES` 三个反例 |
| `tests/test_anonymous_rate_limit.py` | 补 `BUCKET_VOICE` 独立于其他桶 |
| 前端 `useVoiceRecorder.test.ts` | 状态机：权限拒绝、过短拒绝、上传成功、上传失败 |
| 前端 `useSpeech.test.ts` | 状态机：合成成功播放、合成失败、播放中断 |

### 不纳入验收题库

`iteration1-pump-manuals-v1` 测的是问答质量（18/18 正例 + 2/2 负例）。语音是输入输出通道，混入会污染基线语义。

---

## 11. 实现顺序（按依赖）

1. `voice_text.py` + 单测（纯函数，无依赖）
2. `voice_provider.py` + 假 provider 测试（接口先定）
3. 限流桶重构 + 测试（改既有代码，先补测试）
4. `routers/voice.py` + TestClient 测试
5. 公开路由契约登记
6. 前端 composables + 单测
7. ChatView 集成
8. 文档与配置模板更新

每步都遵循 TDD：先写失败测试，再最小实现。

---

## 12. 已知限制

1. **未做流式识别**：首字延迟约 1–2 秒（上传 + ASR 往返）
2. **未做实时打断**：朗读中途需手动停止
3. **TTS 仅单一音色**：`Cherry`，未提供音色选择
4. **未做噪声抑制**：依赖浏览器默认音频处理，工业现场噪声环境下识别率未实测
5. **未在多浏览器验证**：`MediaRecorder` 在 Chrome/Edge 可用，Safari 的 `audio/mp4` 格式未测试
6. **ASR 幻觉风险残余**：三道防线可挡住明显无效输入，但「结构合法 + 内容模糊」的音频仍可能产出错误文字，依赖用户校对
