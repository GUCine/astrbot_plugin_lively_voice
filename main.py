"""機器人偶爾發語音（MiniMax 語音合成）。

- 回覆短、口語化時，私聊約 12%、群聊約 5% 的概率把這句話換成語音發出去；同一個聊天 20 分鐘內最多隨機一次，每天最多 DAILY_MAX 條
- 對方明確要語音（「發個語音」「說給我聽」…）時這一句一定用語音，並提醒模型寫成適合說出口的話
- 帶圖、連結、很長、很多數字的回覆不轉語音；合成失敗照常發文字
- MiniMax 密鑰、音色等在插件配置裡填（WebUI 插件配置，或 data/config/astrbot_plugin_lively_voice_config.json）

主人私信指令：語音 狀態 / 開 / 關 / 試聽 [文字] / 音色 <voice_id>
"""
import asyncio
import base64
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.agent.message import TextPart
from astrbot.core.message.message_event_result import MessageChain, ResultContentType

from .lively_common import Settings, T2S, plugin_data_dir

TZ = timezone(timedelta(hours=8))
P_DM, P_GROUP = 0.12, 0.05
COOLDOWN = 20 * 60
DAILY_MAX = 40
MAX_CHARS, MAX_CHARS_ASKED = 60, 150
ASK_RE = re.compile(r"(发|来|说|用|回|录)(个|段|条|一下|一段|一条)?(语音|声音)|语音(说|回|发|聊|测试|试试|试听)|(测试|试试|试听)语音"
                    r"|(能|会|可以)(发|说)?语音|说给我听|说句话给我听|出个声|听听你的?声音|你的声音")
VOICE_WORD_RE = re.compile(r"语音|声音|出声|说话")
URL_RE = re.compile(r"https?://|www\.|\.com|\.cn")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
KAOMOJI_RE = re.compile(r"[（(][^（）()]{0,12}[ω▽д∀ﾟ°´`･｡◕‿≧≦╥﹏][^（）()]{0,12}[）)]")
ASK_HINT = "[系统提示，不要复述] 这次的回复会被转成语音发出去：写成能直接念出来的一两句口语，不要表情标记、括号动作、链接和列表，也不要说自己发不了语音"
TUNE_FILE = plugin_data_dir("astrbot_plugin_lively_evolve") / "tunables.json"  # 自我成長插件調的參數


def _tune(key: str, default):
    try:
        v = json.loads(TUNE_FILE.read_text("utf-8"))["values"][key]
        return type(default)(v)
    except Exception:
        return default


CAN_SPEAK_HINT = "[系统提示，不要复述] 〈我〉可以发语音：系统会把〈我〉的短回复自动转成语音发出去。聊到语音时不要说自己发不出声音、只能打字"


@register(
    "astrbot_plugin_lively_voice",
    "GUCine",
    "偶爾把短回覆變成語音發出（MiniMax 語音合成），對方要語音時一定用語音",
    "2.0.0",
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
        self.last: dict[str, float] = {}

    def _save(self):
        try:
            self.state_file.write_text(json.dumps(self.state, ensure_ascii=False), "utf-8")
        except Exception as e:
            logger.warning(f"[lively_voice] 保存状态失败: {e}")

    def _cfg(self) -> dict:
        g = self.s.get
        return {"api_key": g("api_key", ""), "api_base": g("api_base", "https://api.minimaxi.com/v1"),
                "group_id": g("group_id", ""), "model": g("model", "speech-02-turbo"),
                "voice_id": g("voice_id", "female-shaonv"), "speed": g("speed", 1.0), "pitch": g("pitch", 0)}

    # ---------- 合成 ----------
    async def tts(self, text: str, voice_id: str | None = None) -> bytes:
        c = self._cfg()
        if not c.get("api_key"):
            raise RuntimeError("还没配置 MiniMax 密钥")
        url = c.get("api_base", "https://api.minimaxi.com/v1").rstrip("/") + "/t2a_v2"
        if c.get("group_id"):
            url += f"?GroupId={c['group_id']}"
        body = {
            "model": c.get("model", "speech-02-turbo"),
            "text": text,
            "stream": False,
            "voice_setting": {"voice_id": voice_id or c.get("voice_id", "female-shaonv"),
                              "speed": float(c.get("speed", 1.0)), "vol": 1.0, "pitch": int(c.get("pitch", 0))},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(url, json=body, headers={"Authorization": f"Bearer {c['api_key']}"}) as r:
                data = await r.json(content_type=None)
        base = data.get("base_resp") or {}
        if base.get("status_code", 0) != 0:
            raise RuntimeError(f"MiniMax 返回错误 {base.get('status_code')}: {base.get('status_msg')}")
        audio = (data.get("data") or {}).get("audio")
        if not audio:
            raise RuntimeError(f"MiniMax 没有返回音频: {str(data)[:200]}")
        return await self._louder(bytes.fromhex(audio))

    @staticmethod
    async def _louder(mp3: bytes) -> bytes:
        """MiniMax 出來的聲音只有 -28 LUFS 左右，QQ 裡聽著很小：用 ffmpeg 拉到 -16 LUFS（失敗就用原聲）"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "32000", "-ac", "1", "-b:a", "128k",
                "-f", "mp3", "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(mp3), timeout=20)
            if proc.returncode == 0 and len(out) > 1000:
                return out
            logger.warning(f"[lively_voice] 调音量失败，用原声: {err[:200]!r}")
        except Exception as e:
            logger.warning(f"[lively_voice] 调音量失败，用原声: {e}")
        return mp3

    @staticmethod
    def _speakable(text: str) -> str:
        t = EMOJI_RE.sub("", KAOMOJI_RE.sub("", text))
        t = re.sub(r"[\[【].{0,10}?[\]】]", "", t)  # [圖片] 之類
        return re.sub(r"\s+", "，", t.strip()).strip("，")

    # ---------- 對方要語音 ----------
    @filter.on_llm_request(priority=3)
    async def mark_ask(self, event: AstrMessageEvent, req):
        if not self.state["enabled"] or not self._cfg().get("api_key"):
            return
        msg = (event.message_str or "").translate(T2S)
        if ASK_RE.search(msg):
            event.set_extra("lively_voice_asked", True)
            req.extra_user_content_parts.append(TextPart(text=ASK_HINT).mark_as_temp())
        elif VOICE_WORD_RE.search(msg):
            req.extra_user_content_parts.append(TextPart(text=self.s.fill(CAN_SPEAK_HINT)).mark_as_temp())

    # ---------- 把回覆換成語音 ----------
    @filter.on_decorating_result(priority=10)
    async def to_voice(self, event: AstrMessageEvent):
        if not self.state["enabled"]:
            return
        result = event.get_result()
        chain = getattr(result, "chain", None)
        if not chain or not isinstance(chain, list) or not all(isinstance(c, Plain) for c in chain):
            return  # 帶圖/表情/@ 的不轉
        if getattr(result, "result_content_type", None) != ResultContentType.LLM_RESULT:
            return  # 只轉模型說的話，指令回覆不轉
        raw = "".join(c.text for c in chain).strip()
        asked = bool(event.get_extra("lively_voice_asked"))
        text = self._speakable(raw)
        if not text or URL_RE.search(raw) or raw.count("\n") > 3 or sum(ch.isdigit() for ch in raw) > 8:
            return
        if len(text) > (MAX_CHARS_ASKED if asked else MAX_CHARS):
            return
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        if self.state["day"] != day:
            self.state["day"], self.state["count"] = day, 0
        if self.state["count"] >= DAILY_MAX or not self._cfg().get("api_key"):
            return
        umo = event.unified_msg_origin
        if not asked:
            p = _tune("voice.p_group", P_GROUP) if event.get_group_id() else _tune("voice.p_dm", P_DM)
            if time.time() - self.last.get(umo, 0) < COOLDOWN or random.random() > p:
                return
        try:
            audio = await self.tts(text)
        except Exception as e:
            logger.warning(f"[lively_voice] 合成失败，照常发文字: {e}")
            return
        result.chain = [Record.fromBase64(base64.b64encode(audio).decode())]
        self.last[umo] = time.time()
        self.state["count"] += 1
        self._save()
        try:
            with (self.dir / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": int(time.time()), "kind": "sent", "umo": umo, "asked": asked}) + "\n")
        except Exception:
            pass
        logger.info(f"[lively_voice] 用语音发了（{'对方要的' if asked else '随机'}，{len(text)} 字）: {umo}")

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
            c = self._cfg()
            reply = (f"语音：{'开' if self.state['enabled'] else '关'}，密钥{'已配' if c.get('api_key') else '没配'}，"
                     f"音色 {c.get('voice_id', 'female-shaonv')}，模型 {c.get('model', '-')}\n"
                     f"今天发了 {self.state['count'] if self.state['day'] == datetime.now(TZ).strftime('%Y-%m-%d') else 0} 条（上限 {DAILY_MAX}）\n"
                     f"私聊约 {round(_tune("voice.p_dm", P_DM) * 100)}%、群聊约 {round(_tune("voice.p_group", P_GROUP) * 100)}% 的短回复会变成语音，别人说「发个语音」时一定用语音")
        elif arg in ("开", "开启"):
            self.state["enabled"] = True
            reply = "好，偶尔发语音"
        elif arg in ("关", "关闭"):
            self.state["enabled"] = False
            reply = "不发语音了"
        elif arg.startswith("试听"):
            words = arg[2:].strip() or self.s.fill("〈主人〉好呀，这是〈我〉的声音")
            try:
                audio = await self.tts(words)
            except Exception as e:
                audio, reply = None, f"试听失败：{e}"
            if audio:
                event.stop_event()
                try:
                    await event.send(MessageChain([Record.fromBase64(base64.b64encode(audio).decode())]))
                except Exception as e:
                    # napcat 發私聊語音常等不到 QQ 的回執，報 1200 Timeout，但語音其實發出去了
                    if "Timeout" not in str(e):
                        await event.send(MessageChain().message(self.s.owner_text(f"语音发送失败：{e}")))
                    else:
                        logger.info("[lively_voice] 语音已发出（napcat 回执超时，可忽略）")
                return
        elif arg.startswith("音色"):
            vid = arg[2:].strip()
            if not vid:
                reply = "格式：语音 音色 female-shaonv"
            else:
                try:
                    await self.tts(self.s.fill("〈我〉换个声音试试"), vid)
                    self.s.cfg["voice_id"] = vid
                    if hasattr(self.s.cfg, "save_config"):
                        self.s.cfg.save_config()
                    reply = f"音色换成 {vid} 了"
                except Exception as e:
                    reply = f"这个音色用不了：{e}"
        if reply is not None:
            self._save()
            event.stop_event()
            await event.send(MessageChain().message(self.s.owner_text(reply)))
