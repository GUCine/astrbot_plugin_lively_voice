# Cantus

> 給〈我〉一個發語音的工具，用不用她自己定（BreezeBlue 雲端語音合成）

AstrBot 插件，屬於「擬真群友」（lively）插件套件。適用 AstrBot >=4.29.0 + NapCat（aiocqhttp）。

主人、群、機器人名字、人設用詞這些共用設定在 [astrbot_plugin_lively_gate](https://github.com/GUCine/astrbot_plugin_lively_gate) 裡填，建議一起裝。

## 功能

```text
〈我〉自己的聲音：工具 lively_voice，她想說的時候用自己的聲音發一條 QQ 語音。

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
```

## 依賴

[BreezeBlue](https://breezeblue.ai/) 的語音合成 API（Breeze TTS 2，要一把 API 密鑰和一個 voice_id）；系統裡有 ffmpeg 時會把整條放大到合適的音量、只壓峰值。

## 配置

在 AstrBot WebUI 的插件配置裡填，或者直接編輯 `data/config/astrbot_plugin_lively_voice_config.json`。留空的公共項（主人、群、機器人名字、平台）會用 astrbot_plugin_lively_gate 裡填的。

| 配置項 | 說明 | 預設 |
| --- | --- | --- |
| `breeze_key` | API 密鑰：在 breezeblue.ai 的開發者頁面建，brz_ 開頭；用 server/mac/set_breeze_key.py 隱藏輸入 | （空） |
| `breeze_base` | 介面地址 | "https://api.breeze.blue/v1" |
| `breeze_model` | 模型：留空讓 BreezeBlue 自己選 | "breeze-tts-2" |
| `breeze_voice_cute` | 可愛那條的 voice_id：公開的聲音、自己設計或克隆後存下的都行（voc_ 開頭） | （空） |
| `breeze_voice_gentle` | 溫柔那條的 voice_id：留空就用可愛那個聲音，加一句「语气温柔，语速稍慢」 | （空） |
| `breeze_guidance` | 語氣說明跟得多緊：她寫了語氣（tone）時送的 guidance_scale，1～10；官方建議有情緒的句子從 1～3 試起，越高越照語氣念、離原本的聲音越遠。沒寫語氣時不送 | 2.0 |
| `loudness` | 發出去的語音多大聲（LUFS）：整條一起放大或縮小到這個平均響度，句子裡的大小聲照留，只壓住快破音的峰值。手機錄的語音大約 -14～-10；越接近 0 越大聲 | -12.0 |
| `owner_qq` | 主人 QQ（留空用門禁插件裡填的） | （空） |

## 資料

執行資料在 `data/plugin_data/astrbot_plugin_lively_voice/`。

## 許可

MIT，見 [LICENSE](LICENSE)。
