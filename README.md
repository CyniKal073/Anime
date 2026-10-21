# 个人动漫媒体库 Release 管理器

输入一个中文动漫名，从 nekoBT 的多个 Release 里挑出符合自己媒体规格要求的版本，
下载到本地，并把其他资源里的中文字幕搬进来。

不是在线观看站，不是资源站，不是播放器，也不管媒体库刮削。

设计文档：[docs/design-nekobt-bangumi.md](docs/design-nekobt-bangumi.md)

---

## 它能做什么

```text
中文作品名
    ↓  本地映射缓存 → Bangumi（中文强项）→ nekoBT 模糊搜索（兜底）
media_id
    ↓  Release 检索 → 标题解析 → 筛选 → 可解释排序
用户选择
    ↓
.torrent  →  qBittorrent（没运行就自动启动）
    ↓
本地视频文件
    ↓  字幕合并（可选）
把「带中文字幕的资源」里的字幕轨搬进你想保留的画质版本，不重编码
```

界面（本地 Web UI）包含：本季连载、搜索、作品候选、筛选排序、Release 表格、下载历史、字幕合并。

下载时可以**每次指定保存目录**（输入框带自动补全：默认目录、它的子文件夹、历史用过的目录），
也可以点「设为默认」写进本地设置——比改 `.env` 方便，而且不用重启。
优先级是：本次指定 > 界面默认 > `.env` 的 `QBIT_SAVEPATH`。

Release 表格可以按四种方式排序：**偏好打分**（1080p / BDRip / HEVC / 简中 优先）、
**发布时间**（新→旧，nekoBT 和蜜柑两个来源都支持）、**做种数**、**体积**。

---

## 环境要求

已在本机验证过的组合：

| 组件 | 版本 / 位置 | 说明 |
| --- | --- | --- |
| Python | 3.9.13（`D:\Anaconda`） | 代码兼容 3.9+ |
| qBittorrent | v4.6.5.10，`F:\qBittorrentEE\qbittorrent_x64.exe` | 需要启用 WebUI |
| MKVToolNix | v96.0，`F:\MKVToolNix` | 字幕提取/合併用，**不需要 ffmpeg** |
| 网络 | 能访问 nekoBT；Bangumi 与封面 CDN 通常需要代理 | 见「已知问题」 |

---

## 安装

```powershell
pip install -r requirements.txt
```

## 配置

复制 `.env.example` 为 `.env`：

```text
# nekoBT 公开检索不需要 Key；只有非公开种子才需要
NEKOBT_API_KEY=

# qBittorrent WebUI（先在客户端里启用：工具 → 选项 → Web UI）
QBIT_URL=http://127.0.0.1:8081
QBIT_USERNAME=admin
QBIT_PASSWORD=你的密码
QBIT_SAVEPATH=D:\Anime\library
QBIT_CATEGORY=anime
QBIT_TAGS=nekobt

# 下载时若客户端没运行，尝试启动它（留空则只报错不启动）
QBIT_EXECUTABLE=F:\qBittorrentEE\qbittorrent_x64.exe

# MKVToolNix 目录（用于字幕提取/合并）
MKV_TOOLS_DIR=F:\MKVToolNix
```

> 本机 8080 端口被 NI Application Web Server 占用，它对 `/api/v2/*` 一律返回 404，
> 所以 qBittorrent WebUI 用的是 **8081**。

---

## 使用

### Web UI（推荐）

```powershell
.\run_web.ps1                      # http://127.0.0.1:8765
.\run_web.ps1 -Port 9000           # 换端口
.\run_web.ps1 -NoAccessLog         # 只保留启动日志
```

用脚本而不是直接 `python -m src.api.app`，是为了固定 UTF-8 日志编码，
否则日志重定向到文件后中文和颜色码会变乱码。

页面上的可见文案、主题色都可以自己改：`src/web/index.html` 顶部有「怎么改」的说明，
主题变量集中在 `src/web/style.css` 的 `:root` 里。

### CLI

```powershell
# 自检
python -m src.main doctor

# 完整流程（交互式选作品与 Release，最后确认下载）
python -m src.main search "葬送的芙莉莲"

# 只检索不下载 / 带上规格偏好 / 只看合集
python -m src.main search "葬送的芙莉莲" --no-download
python -m src.main search "葬送的芙莉莲" --resolution 1080p --source BDRip --codec HEVC --sub-lang zh-hans
python -m src.main search "葬送的芙莉莲" --batch

# 提交到客户端但保持暂停（验证链路时用，不会真的下载）
python -m src.main search "葬送的芙莉莲" --paused
```

CLI 与 Web 共用 `src/service.py`，行为一致。

### 字幕合并

在 Web UI 最下面的「字幕合并」区块：

1. 填「目标视频」（想保留的画质）和「来源视频」（带中文字幕的），两个输入框会从下载目录自动补全
2. 点「读取来源的字幕轨」→ 列出所有字幕轨，**中文轨自动勾选**
3. 选语言、轨道名、是否设为默认字幕
4. 「开始合并」→ 在目标视频旁边生成 `原名.subbed.mkv`

规则：

* 只复制字幕轨，**视频/音频不重编码**，画质不受影响，通常几秒完成
* 默认**不动原文件**，在旁边生成 `原名.subbed.mkv`（也拒绝把输出写到与目标相同的路径）
* 想省空间可以勾「嵌入后替换原文件」：新文件生成成功后顶替原视频，不再多留一份。
  这**不可逆**，建议先拿一两个文件试。删之前会核对新文件大小——明显变小就说明
  mkvmerge 少拷了流，此时宁可放弃替换，也不会把原片弄丢
* 提取出的字幕文件保留在 `data/subtitles/`，可以单独外挂使用
* 只能处理**内封字幕轨**；烧进画面的硬字幕提取不了

### 本地字幕文件

手里已经有一个 `.ass` / `.srt`（射手网下回来的、或者资源站附带的），不用再去找
「带内嵌字幕的来源视频」：

* 点「选择文件」→ 复用上面那个目录弹窗，只是切成了选文件模式，只列
  `.ass` / `.ssa` / `.srt` / `.vtt` / `.sup` / `.sub`
* 或者直接从资源管理器把字幕文件**拖到那一行**上。浏览器拿不到本地文件的绝对路径，
  所以拖拽走的是上传：文件会存进 `data/subtitles/`，再拿那份副本去嵌入
* 语言 / 轨道名 / 设为默认 / 输出文件名 / 替换原文件这些选项，和上面的合并、嵌入共用

---

## 测试

```powershell
python -m pytest      # 179 个用例，全部离线，不联网、不调用外部工具
```

其中 `tests/test_web_assets.py` 会检查 **app.js 引用的每个元素 id 都存在于 index.html**——
改前端时如果改错了 id，测试会直接报出来。

---

## 目录结构

```text
src/
├── main.py                 CLI 入口
├── service.py              CLI 与 Web 共用的业务编排层（搜索/Release/下载/字幕/自检）
├── config.py               .env 读取与校验
├── storage.py              SQLite（映射、下载历史、设置）+ JSON 缓存
├── net.py                  IPv6/IPv4 自适应连接
├── api/app.py              Web API（FastAPI）
├── web/                    前端（原生 HTML/CSS/JS，无构建步骤）
├── nekobt/
│   ├── client.py           HTTP 客户端（媒体/Torrent 检索、.torrent 下载）
│   └── media.py            中文名 → media_id（季数剥离、繁简归一、置信度分档）
├── bangumi/client.py       中文检索入口 + 每日放送 + 封面
├── release/
│   ├── models.py           领域模型
│   ├── parser.py           标题解析（分辨率/来源/编码/集数/合集）
│   └── matcher.py          三态筛选与可解释排序
├── qbittorrent/
│   ├── client.py           WebAPI v2
│   └── launcher.py         按需启动客户端
└── mkv/tools.py            MKVToolNix 封装（探测/提取/合并字幕）
```

运行期产生的数据都在 `data/`（已 gitignore）：

```text
data/anime_release_manager.db   映射、下载历史、偏好设置
data/cache/                     搜索结果与桥接结果的 JSON 缓存
data/subtitles/                 字幕提取的中间产物
```

---

## 性能与缓存

实测耗时（代理开启时）：

| 操作 | 首次 | 之后 |
| --- | --- | --- |
| 搜索作品 | 2.2 – 3.7 s | 0.00 s |
| 点选候选（解析 nekoBT 资源） | 2.0 s | 0.00 s |
| 打开页面自检 | 2 – 3 s | — |

多级缓存：

| 缓存 | 位置 | 有效期 | 作用 |
| --- | --- | --- | --- |
| 用户确认的映射 | SQLite `media_mapping` | 长期 | Bangumi 不可用时的兜底；标记「上次选择」 |
| Bangumi 搜索结果 | `data/cache` | 10 分钟 | 同一关键词不再重复打接口 |
| Bangumi → nekoBT 桥接 | `data/cache` | 7 天 | 同一作品只桥接一次 |
| nekoBT 媒体详情 | `data/cache` | 7 天 | 标题与封面 |
| 本季连载 | `data/cache` | 30 分钟 | 每日放送 |

搜索采用**惰性桥接**：搜索阶段只查 Bangumi（约 2 秒出结果），
`media_id` 留到你点选那一刻才解析——不然每次搜索都要为 5 个候选白等 4 秒。

---

## 已知问题与踩坑

这些都在设计文档附录 A 里有记录，代码里也做了防护，改代码时别绕过去。

**接口与数据**

1. **nekoBT 搜索参数是 `query`，不是 `q`。** 写错不报错，只会静默忽略搜索条件、返回最新 50 条。
2. **中文只能搜媒体，不能搜 Torrent。** Torrent 标题是罗马字，中文必须走 `media/search`。
3. **带季数后缀的中文查询会命中错误作品**（`无职转生 第二季` → `Biaoren`），所以先剥离季数后缀。
4. **`video_codec` 是数字枚举**（1=H.264，2=HEVC，3=AV1），传字符串会 500。
5. **字幕语言参数是 `sub_lang`**，不是 `subtitle_language`。
6. **`video_type` 语义未明**（3/7/8/9/12/13/15 混杂且是精确匹配），所以来源筛选走标题解析后的客户端过滤。
7. **`resolution` / `source` 不在 API 返回里**，必须从标题解析。
8. **nekoBT 的模糊匹配是字符级的**，没有分词也没有语义。精确命中给 1.0，繁简差异约 0.5，噪声 0.1~0.2 且没有相关性阈值——所以候选要按置信度分档，不能直接信排序。
9. **mkvmerge 的 JSON 里 `codec` 是人名**（`SubRip/SRT`），Matroska 编码 ID 在 `properties.codec_id`（`S_TEXT/UTF8`）。扩展名映射要认两套，否则 SRT 会被当成 ASS 提取出来。

**下载器**

10. **qBittorrent 的暂停/恢复接口名随版本变化**：4.6 只有 `pause`/`resume`，5.0+ 才是 `stop`/`start`；客户端做的是「新名优先、404 回退」。添加任务的暂停字段同理（`paused` vs `stopped`）。
11. **连接被拒不能变成 500。** 客户端没启动时抛的是 `requests.ConnectionError`，必须转成可读错误，否则接口直接 5xx。
12. **不要在打开页面时探测 qBittorrent。** 用户可能几小时不下载一次，那时它必然是关着的。改成下载时按需启动。

**网络**

13. **DNS 污染的方向是分域名的。** `nekobt.to` 实测是 A 记录被污染（IPv6 正常），而 `api.bgm.tv` 反过来（AAAA 指向 Facebook 的 IP，IPv4 才是真的）。`src/net.py` 因此不写死顺序，而是**记住每个主机实际可用的地址族**，并给单个地址 5 秒连接上限——否则一个不通的地址会把整体拖到请求超时（实测出现过单次桥接 12 秒）。
14. **封面 CDN 需要单独走代理。** 封面来自 `lain.bgm.tv`，实测即使 VPN 开着也连不上（DoH 查出来的还是 Facebook 的 IP）。数据和 URL 都是对的，只是浏览器取不到图，页面会显示「无图」占位。

**前端**

15. **静态资源必须不缓存。** 曾经用 `?v=2` 手工版本号，改了 `app.js` 却忘了改版本号，浏览器一直用旧代码，表现是「新功能看不见」。现在 `index.html` 与 `/static/*` 都是 `Cache-Control: no-store`。
16. **改 HTML 可以改文案，不要改 `id`。** 带 `id` 的元素是脚本的取值钩子；`tests/test_web_assets.py` 会守住这条。

**设计与缓存（踩过的坑）**

17. **缓存不能短路搜索结果。** 「已确认映射」曾经直接返回单条结果，导致多季作品选过一次就再也改不了。现在它只做标记：正常搜索照做，上次选的那条置顶并标「上次选择」；只有 Bangumi 不可用时才当兜底通道。
18. **可用性记忆化会造成假降级。** 用带 TTL 的 `is_available()` 做前置判断时，一次偶发失败会让之后 60 秒的所有搜索都降级到 nekoBT 模糊搜索。现在直接尝试、失败才降级。

**射手网（字幕库）**

19. **同一条搜索结果里混着两套字段名**（老结构 `id`/`subtype`/`upload_time`，新结构 `fileid`/`m_subtype`/`uploadtime`）。只认一套就会有一半结果「无编号」——那批结果的编号取不到，点「嵌入」直接报错。
20. **搜索对简繁敏感。** 简体查询和繁体查询返回的是两个不同结果集，所以两种写法都查一遍再按 `sub_id` 合并；代价是**每次搜索花 2 次配额**。
21. **配额按账号发，别信文档写的 20 次/分钟。** 实测账号只有 5 次/分钟，所以 `ASSRT_MIN_INTERVAL` 默认 12 秒（重试也要等够这个间隔，不能插队）。只有 search / detail 占配额，字幕文件本身不占。
22. **字幕文件下载偶发 HTTP 502。** 这是射手网自己的下载服务器，挂了代理时更容易碰到；因为重试不占配额，`download()` 自己重试 3 次，4xx 不重试（那是文件真的没了）。

---

## 待办

- [x] qBittorrent WebUI 自检通过（WebAPI 2.9.3 / 应用 v4.6.5.10）
- [x] 真实种子验证「获取 `.torrent` → 提交 → 客户端出现任务 → 正常下载」
- [x] 接入 Bangumi 作为中文检索第一入口（含降级）
- [x] 首页本季连载 + 封面
- [x] qBittorrent 按需启动
- [x] 内封字幕提取与合并
- [ ] nekoBT 非公开种子（`private_magnet`）的 API Key 形式尚未验证
- [ ] 封面 CDN（`lain.bgm.tv`）在当前网络不可达，需要代理规则配合
- [ ] Release 的季度归属：nekoBT 的 `media_id` 是整季合一的粒度，选「第二季」也会列出全系列的种子
