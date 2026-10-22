# 語音回覆

> 偶爾把短回覆變成語音發出（MiniMax 語音合成），對方要語音時一定用語音

AstrBot 插件，屬於「擬真群友」（lively）插件套件。適用 AstrBot >=4.29.0 + NapCat（aiocqhttp）。

主人、群、機器人名字、人設用詞這些共用設定在 [astrbot_plugin_lively_gate](https://github.com/GUCine/astrbot_plugin_lively_gate) 裡填，建議一起裝。

## 功能

```text
機器人偶爾發語音（MiniMax 語音合成）。

- 回覆短、口語化時，私聊約 12%、群聊約 5% 的概率把這句話換成語音發出去；同一個聊天 20 分鐘內最多隨機一次，每天最多 DAILY_MAX 條
- 對方明確要語音（「發個語音」「說給我聽」…）時這一句一定用語音，並提醒模型寫成適合說出口的話
- 帶圖、連結、很長、很多數字的回覆不轉語音；合成失敗照常發文字
- MiniMax 密鑰、音色等在插件配置裡填（WebUI 插件配置，或 data/config/astrbot_plugin_lively_voice_config.json）

主人私信指令：語音 狀態 / 開 / 關 / 試聽 [文字] / 音色 <voice_id>
```

## 依賴

[MiniMax](https://www.minimaxi.com/) 語音合成 API；系統裡有 ffmpeg 時會把音量拉到合適的大小。

## 配置

在 AstrBot WebUI 的插件配置裡填，或者直接編輯 `data/config/astrbot_plugin_lively_voice_config.json`。留空的公共項（主人、群、機器人名字、平台）會用 astrbot_plugin_lively_gate 裡填的。

| 配置項 | 說明 | 預設 |
| --- | --- | --- |
| `api_key` | MiniMax API 密鑰：沒填就不會發語音 | （空） |
| `api_base` | MiniMax 介面地址：國內用 https://api.minimaxi.com/v1，海外用 https://api.minimax.io/v1 | "https://api.minimaxi.com/v1" |
| `group_id` | MiniMax Group ID：老帳號需要，新帳號可以留空 | （空） |
| `model` | 語音合成模型 | "speech-02-turbo" |
| `voice_id` | 音色 ID：主人私信「語音 音色 xxx」也能改 | "female-shaonv" |
| `speed` | 語速 | 1.0 |
| `pitch` | 音調 | 0 |
| `owner_qq` | 主人 QQ（留空用門禁插件裡填的） | （空） |

## 資料

執行資料在 `data/plugin_data/astrbot_plugin_lively_voice/`。

## 許可

MIT，見 [LICENSE](LICENSE)。
