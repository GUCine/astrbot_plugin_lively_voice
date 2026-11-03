"""〈我〉自己的聲音：工具 lively_voice，她想說的時候用自己的聲音發一條 QQ 語音。

- 10-29 起改成她自己調用（用戶定：做成她能調的工具，用不用看她自己）。以前「短回覆按機率換成語音」「對方要語音時一定換」都拿掉了
- 聲音走 BreezeBlue 雲端（Breeze TTS 2）：可愛／溫柔各填一個 voice_id（溫柔沒填就用可愛那個加一句語氣說明）；她能帶一句語氣說明（tone），
  句子裡能放 [笑] [叹气] 這類聲音標籤。11-03 用戶說舊的下線剔除：本機 CosyVoice3、MiniMax 兩條路連同設定都拿掉了（舊檔在 v195 版本包）
- 工具能用時，每輪 system 最後接一段 VOICE_RULE：讓她知道自己有聲音、念得有情緒要怎麼寫（11-03 用戶說「要让她自己知道可以发语音」「表达出正确的情感，不至于干巴巴」）；
  只講能做什麼、怎麼寫才念得出來，不叫她多用。語氣的寫法照 BreezeBlue 官方 voice-instruction-prompting、audio-tags 兩頁
- 合成好的用 ffmpeg 整條放大到 loudness（預設 -12 LUFS）、只壓峰值再發（11-03 起，以前 loudnorm 拉到 -16）；
- 每條記進 events.jsonl（units＝BreezeBlue 計費單位），飯錢插件照 BreezeBlue 的用量介面記實際扣的點數；合成失敗照實回給她，什麼都不發
- 一條最多 MAX_CHARS 字；工具等 WAIT 秒，長句沒合成完轉背景、好了自己發，結果記進那個聊天的 outbox
- 一天最多 DAILY_MAX 條（防失控）；同一時間只合成一句

主人私信指令：語音 狀態 / 開 / 關 / 試聽 [溫柔] [文字]
"""
import asyncio
import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Record
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.message.message_event_result import MessageChain

from .lively_common import ASK_OWNER, Settings, T2S, note_outbox

TZ = timezone(timedelta(hours=8))
DAILY_MAX = 40
MAX_CHARS = 300
WAIT = 100   # 工具最多等多久（AstrBot 工具上限 120 秒）；長句沒合成完轉背景，好了自己發
STYLES = {"cute": "可爱", "gentle": "温柔"}
STYLE_ALIASES = {"可爱": "cute", "温柔": "gentle", "": "cute"}
URL_RE = re.compile(r"https?://\S+|www\.\S+")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
KAOMOJI_RE = re.compile(r"[（(][^（）()]{0,12}[ω▽д∀ﾟ°´`･｡◕‿≧≦╥﹏][^（）()]{0,12}[）)]")
ACTION_RE = re.compile(r"[（(][^（）()]{0,16}[）)]")  # 括號裡的動作、神態，念不出來
TAG_RE = re.compile(r"&&[^&]{1,20}&&|[\[【][^\]】]{0,10}[\]】]")  # 表情包標籤、[圖片] 之類
# BreezeBlue 認的中文聲音標籤（docs.breezeblue.ai/guides/audio-tags 的 zh 那張表），留著
BREEZE_TAGS = ("笑 轻笑 咯咯笑 哭声 抽泣 呜咽 呻吟 哀号 叹气 惊呼 吸气 呼气 喘息 耳语 喊叫 惨叫 唱歌 哼歌 结巴 停顿 "
               "清嗓 咳嗽 吸鼻子 咂嘴 啧嘴 打哈欠 打喷嚏 打嗝 打饱嗝 吞咽 干呕 闷哼 冷笑 哼鼻子").split()
BREEZE_TAG_RE = re.compile(r"\[(" + "|".join(BREEZE_TAGS) + r")\]")
TONE_MAX = 300
# 每輪接在 system 最後（固定字，DeepSeek 前綴快取命中得到）
VOICE_RULE = """[〈我〉的声音]
〈我〉有自己的声音：想说出口的时候，可以用 lively_voice 发一条语音到正在聊的这个聊天，跟打字、发表情包一样是〈我〉说话的一种方式。用不用、什么时候用、说什么由〈我〉定。
念出来有没有情绪，靠两样（不写就照这个声音平常的样子念，起伏比较平）：
- tone：用中文写一两句「这句怎么念」，从下面挑两三样写就够，不用全写：
  情境（躲着人说悄悄话、刚被夸了、在哄一个着急的人、困得睁不开眼）；
  想达到什么（撒娇、安抚、逞强、装没事、吓唬人、炫耀）；
  情绪和分寸（「有点不耐烦但不凶」「开心得藏不住」「委屈但不哭」）；
  节奏和重音（「“现在”加重，别喊」「说到一半停一下」「越说越快」「尾音拖长」）；
  身体和嗓子（「刚睡醒有点哑」「憋着眼泪，声音发颤」「跑过来喘着气」「捂着嘴偷笑」）。
  不用写声音是谁（年纪、口音是选好的声音定的），别写互相打架的（「大声地耳语」），也不用保证几秒停顿。
  同一句「你怎么又来了」：tone 写「惊喜，越说越高兴，像盼了很久」是一种；写「懒洋洋，拖长音，假装嫌弃其实在笑」是另一种；写「压低声音，很紧张，怕被别人听见」又是一种。
- 声音标签：在 text 里声音出现的那个位置写方括号标签，照下面的字写，不要自己编：
  [笑] [轻笑] [咯咯笑] [叹气] [吸气] [呼气] [喘息] [惊呼] [耳语] [喊叫] [惨叫] [唱歌] [哼歌] [结巴] [停顿]
  [哭声] [抽泣] [呜咽] [吸鼻子] [清嗓] [咳嗽] [打哈欠] [打喷嚏] [打嗝] [吞咽] [咂嘴] [啧嘴] [冷笑] [哼鼻子] [闷哼]
  例如「哼，[叹气] 好吧，本鱼原谅你了 [轻笑]」。tone 管整句的念法，标签管某一处的声音，两样可以一起用。"""
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def speakable(text: str) -> str:
    """去掉念不出來的：表情、顏文字、括號動作、標籤、鏈接；空白換成逗號。BreezeBlue 的聲音標籤留著"""
    t = URL_RE.sub("", text or "")
    t = BREEZE_TAG_RE.sub(lambda m: f"\x00{BREEZE_TAGS.index(m.group(1))}\x01", t)
    t = TAG_RE.sub("", KAOMOJI_RE.sub("", t))
    t = ACTION_RE.sub("", EMOJI_RE.sub("", t))
    t = re.sub(r"\s+", "，", t.strip())
    t = re.sub(r"，{2,}", "，", t).strip("，")
    t = re.sub("\x00(\\d+)\x01", lambda m: f"[{BREEZE_TAGS[int(m.group(1))]}]", t)
    return t


def billable(text: str) -> int:
    """BreezeBlue 的計費單位：中日韓字算 2，其他字元算 1；10 單位＝1 點"""
    cjk = len(CJK_RE.findall(text))
    return cjk * 2 + (len(text) - cjk)


def is_timeout(e: Exception) -> bool:
    t = str(e)
    return "1200" in t or "Timeout" in t or "timeout" in t or isinstance(e, asyncio.TimeoutError)


@register(
    "astrbot_plugin_lively_voice",
    "GUCine",
    "〈我〉自己的聲音：她想說的時候調工具發一條 QQ 語音（BreezeBlue 雲端）",
    "2.3.0",
)
class LivelyVoice(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.s = Settings(context, config)
        self.dir = Path(StarTools.get_data_dir("astrbot_plugin_lively_voice"))
        self.state_file = self.dir / "state.json"
        try:
            self.state = json.loads(self.state_file.read_text("utf-8"))
        except Exception:
            self.state = {}
        self.state.setdefault("enabled", True)
        self.state.setdefault("day", "")
        self.state.setdefault("count", 0)
        self._lock = asyncio.Lock()

    def _save(self):
        try:
            self.state_file.write_text(json.dumps(self.state, ensure_ascii=False), "utf-8")
        except Exception as e:
            logger.warning(f"[lively_voice] 保存状态失败: {e}")

    def _log(self, **kw):
        try:
            with (self.dir / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": int(time.time()), **kw}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _today(self) -> int:
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        if self.state["day"] != day:
            self.state["day"], self.state["count"] = day, 0
        return self.state["count"]

    def _where(self) -> str:
        return f"BreezeBlue 云端（{self.s.get('breeze_model', 'breeze-tts-2')}，声音 {self.s.get('breeze_voice_cute', '') or '还没选'}）"

    def _ready(self) -> str:
        """不能用的原因；能用回空"""
        if not self.state["enabled"]:
            return "语音关着"
        if not self.s.get("breeze_key", ""):
            return "还没配置 BreezeBlue 密钥"
        if not str(self.s.get("breeze_voice_cute", "")).strip():
            return "还没选 BreezeBlue 的声音（breeze_voice_cute）"
        return ""

    # ---------- 合成 ----------
    async def tts(self, text: str, style: str = "cute", tone: str = "") -> tuple[bytes, float]:
        """回 (mp3, 秒數)"""
        return await self._breeze(text, style, tone)

    async def _breeze(self, text: str, style: str, tone: str) -> tuple[bytes, float]:
        g = self.s.get
        voice = str(g("breeze_voice_gentle", "") if style == "gentle" else "").strip()
        if style == "gentle" and not voice and not tone:
            tone = "语气温柔，语速稍慢"
        voice = voice or str(g("breeze_voice_cute", "")).strip()
        url = (str(g("breeze_base", "https://api.breeze.blue/v1")).rstrip("/")
               + f"/text-to-speech/{voice}?output_format=mp3_24000_128")
        body = {"text": text, "model_id": str(g("breeze_model", "breeze-tts-2")).strip() or None, "language_code": "zh"}
        if tone:
            # 官方：guidance_scale 預設 1.0、有情緒的句子從 1～3 試起；不寫 tone 就留預設（貼近參考音）
            body["instructions"] = tone
            body["voice_settings"] = {"guidance_scale": float(g("breeze_guidance", 2.0) or 2.0)}
        head = {"xi-api-key": str(g("breeze_key", "")), "User-Agent": "lively-voice/2.2", "Accept": "audio/mpeg"}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=WAIT - 10)) as s:
            async with s.post(url, json=body, headers=head) as r:
                raw = await r.read()
                if r.status != 200:
                    try:
                        err = json.loads(raw)
                        why = f"{err.get('code', '')} {err.get('detail', '')}".strip()
                    except Exception:
                        why = raw[:80].decode("utf-8", "replace")
                    raise RuntimeError(f"BreezeBlue 回了 {r.status}：{why[:100]}")
        if len(raw) < 1000:
            raise RuntimeError("BreezeBlue 没有返回音频")
        return await self._to_mp3(raw, "mp3"), 0.0

    @staticmethod
    async def _loudness(raw: bytes, fmt: str) -> float | None:
        """整條的平均響度（LUFS）；量不出來回 None"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-nostats", "-f", fmt, "-i", "pipe:0", "-af", "ebur128", "-f", "null", "-",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(raw), timeout=20)
            got = re.findall(r"I:\s+(-?[\d.]+) LUFS", err.decode("utf-8", "replace"))
            return float(got[-1]) if got and float(got[-1]) > -70 else None
        except Exception:
            return None

    async def _to_mp3(self, raw: bytes, fmt: str) -> bytes:
        """轉 mp3、整條一起放大到 loudness（預設 -12 LUFS，跟手機錄的語音差不多大聲），只壓住超過 -1.5 dBFS 的峰值（轉 mp3 會再冒一點）；
        句子裡的大小聲照原樣留著（11-03 用戶說「声音有大有小很正常」、QQ 裡看語音條音量太低；以前 loudnorm 拉到 -16）。
        ffmpeg 失敗時照原樣發"""
        target = float(self.s.get("loudness", -12) or -12)
        now = await self._loudness(raw, fmt)
        gain = min(max(target - now, -10.0), 24.0) if now is not None else 0.0
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", fmt, "-i", "pipe:0",
                "-af", f"volume={gain:.1f}dB,alimiter=limit=0.84:level=false", "-ar", "32000", "-ac", "1", "-b:a", "128k",
                "-f", "mp3", "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(raw), timeout=20)
            if proc.returncode == 0 and len(out) > 1000:
                return out
            logger.warning(f"[lively_voice] 转 mp3 失败，用原样: {err[:200]!r}")
        except Exception as e:
            logger.warning(f"[lively_voice] 转 mp3 失败，用原样: {e}")
        return raw

    async def _send(self, event: AstrMessageEvent, audio: bytes) -> str:
        """發到這個聊天；回空＝發了，否則是原因。NapCat 發語音常等不到 QQ 回執報 1200 Timeout，其實發出去了"""
        try:
            await event.send(MessageChain([Record.fromBase64(base64.b64encode(audio).decode())]))
        except Exception as e:
            if not is_timeout(e):
                return f"发送时出错了（{str(e)[:60]}）"
            logger.info("[lively_voice] 语音发出去了（napcat 回执超时，可忽略）")
        return ""

    # ---------- 工具不能用時拿掉 ----------
    @filter.on_llm_request(priority=100)
    async def hide_voice_tool(self, event: AstrMessageEvent, req=None):
        tools = getattr(req, "func_tool", None)
        if not tools:
            return
        if self._ready():
            try:
                if tools.get_tool("lively_voice"):
                    tools.remove_tool("lively_voice")
            except Exception:
                pass
            return
        try:
            if not tools.get_tool("lively_voice"):  # 別的插件這一輪拿掉了（例如在做自己記的事）就不提
                return
        except Exception:
            return
        rule = self.s.fill(VOICE_RULE)
        sp = getattr(req, "system_prompt", "") or ""
        if rule not in sp:
            req.system_prompt = f"{sp.rstrip()}\n\n{rule}" if sp.strip() else rule

    @filter.llm_tool("lively_voice")
    async def tool_voice(self, event: AstrMessageEvent, text: str = "", style: str = "cute", tone: str = ""):
        """用你自己的声音说一句，作为一条 QQ 语音发到这个聊天。用不用、什么时候用、说什么由你。
        念得有没有情绪看 tone 和声音标签，怎么写见 system 里「〈我〉的声音」那段。

        Args:
            text(string): 要念出来的话，最多 300 字。表情、括号里的动作、链接、表情包标签念不出来，会去掉；[笑] [叹气] [耳语] 这类声音标签照写在声音出现的位置
            style(string): cute 可爱（默认）或 gentle 温柔
            tone(string): 可不填。这句怎么念，用中文写一两句：情境、想达到什么、情绪和分寸、节奏重音、嗓子状态里挑两三样，例如「刚睡醒，懒洋洋拖长音，有点撒娇」「憋着笑装生气，“才没有”加重」
        """
        why = self._ready()
        if why:
            return f"没发：{why}"
        style = STYLE_ALIASES.get(str(style or "").strip(), str(style or "").strip().lower())
        if style not in STYLES:
            return "没发：style 只能是 cute（可爱）或 gentle（温柔）"
        words = speakable(text)
        tone = re.sub(r"\s+", " ", str(tone or "")).strip()[:TONE_MAX]
        if not BREEZE_TAG_RE.sub("", words).strip("，"):
            return "没发：这句去掉表情、动作、链接以后没有能念的字了"
        if len(words) > MAX_CHARS:
            return f"没发：一条语音最多念 {MAX_CHARS} 字，这句有 {len(words)} 字"
        if self._today() >= DAILY_MAX:
            return f"没发：今天已经发了 {DAILY_MAX} 条语音，是一天的上限（防失控的，谁叫的都算）" + ASK_OWNER
        job = asyncio.ensure_future(self._say(event, words, style, tone))
        try:
            return await asyncio.wait_for(asyncio.shield(job), timeout=WAIT)
        except asyncio.TimeoutError:
            job.add_done_callback(lambda j: self._late(event.unified_msg_origin, j))
            logger.info(f"[lively_voice] 语音合成超过 {WAIT} 秒，转到背景，好了自己发")
            return (f"还在合成（{len(words)} 字，已经等了 {WAIT} 秒），好了会自己发到这个聊天；"
                    f"这一条回复里你接着写的话会先作为文字发出去")

    def _late(self, umo: str, job):
        """轉背景的那條做完了：結果記一筆給這個聊天，她下一輪看得到"""
        try:
            r = job.result()
        except Exception as e:
            r = f"没发：{type(e).__name__}"
        note_outbox(umo, f"刚才那条在背景合成的语音：{r.split('。')[0]}", kind="后台:语音")

    async def _say(self, event, words: str, style: str, tone: str = "") -> str:
        umo = event.unified_msg_origin
        t0 = time.time()
        async with self._lock:
            try:
                audio, secs = await self.tts(words, style, tone)
            except Exception as e:
                logger.warning(f"[lively_voice] 合成失败: {e}")
                self._log(kind="fail", umo=umo, style=style, error=str(e)[:120])
                return f"没发：声音没合成出来（{str(e)[:60] or type(e).__name__}），这个聊天里什么都没发出去"
        err = await self._send(event, audio)
        if err:
            self._log(kind="fail", umo=umo, style=style, error=err)
            return f"没发：{err}"
        self.state["count"] += 1
        self._save()
        took = time.time() - t0
        secs = secs or len(audio) * 8 / 128000  # 沒回秒數的照 128k mp3 估
        extra = {"engine": "breeze", "tone": tone, "units": billable(words)}
        self._log(kind="said", umo=umo, style=style, chars=len(words), secs=round(secs, 1), took=round(took, 1),
                  asker=str(event.get_sender_id() or ""), **extra)
        logger.info(f"[lively_voice] 她发了语音（{STYLES[style]}，{len(words)} 字，{secs:.1f} 秒音频，等了 {took:.0f} 秒）: {umo}")
        dur = f"，约 {secs:.0f} 秒" if secs else ""
        how = f"，语气「{tone}」" if tone else ""
        return (f"语音发出去了（{STYLES[style]}的声音{how}，念的是「{words}」{dur}）。"
                f"这一条回复里你接着写的话，会另外作为文字发出去")

    # ---------- 主人指令 ----------
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=1440)
    async def owner(self, event: AstrMessageEvent):
        if not self.s.is_owner(event.get_sender_id()):
            return
        text = (event.message_str or "").strip().translate(T2S)
        if text in ("语音测试", "测试语音", "语音试听", "试听语音", "试听"):
            text = "语音 试听"
        if not text.startswith("语音 ") and text != "语音":
            return
        arg = text[2:].strip()
        reply = None
        if arg in ("", "状态"):
            where = self._where()
            why = self._ready()
            reply = (f"语音：{'开' if self.state['enabled'] else '关'}{('（' + why + '）') if why and self.state['enabled'] else ''}，声音用 {where}\n"
                     f"发不发她自己定（工具 lively_voice）；今天发了 {self._today()} 条（上限 {DAILY_MAX}）")
        elif arg in ("开", "开启"):
            self.state["enabled"] = True
            reply = "语音：开了，她想发的时候可以用自己的声音发语音"
        elif arg in ("关", "关闭"):
            self.state["enabled"] = False
            reply = "语音：关了，她这边的语音工具拿掉了"
        elif arg.startswith("试听"):
            rest = arg[2:].strip()
            style = "cute"
            for k, v in STYLE_ALIASES.items():
                if k and rest.startswith(k):
                    style, rest = v, rest[len(k):].strip()
            words = speakable(rest) or self.s.fill("〈主人〉好呀，这是〈我〉的声音")
            try:
                async with self._lock:
                    audio, _ = await self.tts(words[:MAX_CHARS], style)
            except Exception as e:
                audio, reply = None, f"试听失败：{e}"
            if audio:
                event.stop_event()
                err = await self._send(event, audio)
                if err:
                    await event.send(MessageChain().message(self.s.admin_text(f"语音{err}")))
                return
        if reply is not None:
            self._save()
            event.stop_event()
            await event.send(MessageChain().message(self.s.admin_text(reply)))
