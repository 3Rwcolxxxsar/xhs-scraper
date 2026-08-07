## 目标
使用真实账号和 Cookie 调用小红书 Web API，支持按多个关键词搜索，按照设置的筛选条件（默认按综合自然排序），抓取前n个帖子的字段信息，按照要求的输出格式通过API上传至数据库。
抓取的字段：正文、作者、图片、时间、点赞、评论总数、全量顶层评论、全量子评论

## 解决的问题
xiaohongshu-CLI只能读取帖子每条顶层评论下的第一条子评论，无法完整读取“展开”后的全部子评论
get_sub_comments() 没有复用 get_comments() 的 xsec token 解析和失效重试逻辑
不具备采集限频和风控策略
设置了单帖180秒硬超时，可能导致较大的抓取任务被强行截断
设置了超过300条评论的帖子就跳过不抓取的逻辑
CLI锁定了依赖库xhshow（https://pypi.org/project/xhshow/）版本，导致旧版签名器不兼容当前子评论接口，CLI对相同参数返回code=-1，子评论提取不全

## 使用方法
1、下载xhs-scraper.skill、xiaohongshu-CLI
2、告诉Agent关键词+筛选条件+帖子抓取数量
  支持设置的筛选条件：
    排序：general、latest、most-liked、most-commented、most-collected
    发布时间：all、day、week、half-year
    搜索范围：all、seen、unseen、followed
    “最多评价”映射为小红书接口的 comment_descending

#### 输入的提示词示例：
/xhs-scraper.skill
  关键词：
  - 美国货代
  - 美加线
  - 美国海运
  每个关键词：5 个帖子

Agent实际运行命令：
  cd /Users/3rw_colxxxsar/Documents/xiaohongshu-cli-main
  
  uv run python scrape_and_sync.py \
    --keyword "美国货代" \
    --keyword "美加线" \
    --keyword "美国海运" \
    --limit 5
    
抓取流程固定为：
  自然排序搜索
  → 每词前 n 个唯一帖子
  → 读取正文、作者、图片、时间、点赞、评论总数
  → 顶层评论逐页
  → 子评论逐页
  → 完整 payload 落盘
  → 上传 API

## 新增的功能
### 子评论增加：
--xsec-token 解析和失效重试逻辑
URL / 短索引解析
--all 全量提取
token 自动刷新


#### 解决同步脚本因 180 秒总超时导致评论不全的问题
主要修改在 [sync_xhs.py](/Users/3rw_colxxxsar/Documents/xiaohongshu-cli-main/sync_xhs.py)：
保留每次 HTTP 请求自身的超时、重试和风控策略。
每抓完一页顶层评论或子评论，立即原子写入 checkpoint。

#### 支持对较大的任务自动分轮执行、断点恢复、去重
断点恢复分为任务级和评论级两层。如果中途超时，直接再次运行同一命令即可续抓。

##### 任务级 checkpoint
保存在：.xhs-scrape-runs/<关键词-数量-hash>/manifest.json
相同关键词和数量会生成相同运行目录。再次执行相同命令时，会读取已有 manifest，按note_id去重后继续抓取。
每个帖子有以下状态：
partial：评论未抓完，下次继续。
ready：dry-run 已抓完整，payload 已生成。
uploaded：已经上传，后续不会重复上传。
failed：非风控类错误，需要检查错误信息。

##### 评论级 checkpoint 
位于：.xhs-scrape-runs/<任务>/comments/<note_id>.json
它保存：
{
  "top_cursor": "下一页顶层评论游标",
  "top_complete": false,
  "seen_top_cursors": [],
  "comments": [],
  "reply_state": {
    "父评论ID": {
      "cursor": "下一页子评论游标",
      "complete": false,
      "seen_cursors": []
    }
  }
}
每成功抓完一页，就先写入临时文件，再用原子替换更新 checkpoint。因此即使下一页超时，上一页数据也不会丢。
网络超时或风控暂停后，下次运行从保存的 cursor 继续。
comment ID 去重并检测重复 cursor，防止分页死循环。
评论未完整时标记为 partial，不会上传部分评论覆盖远端完整数据。


#### 遇到卡点具备回退策略，不直接打断抓取过程
只有登录、验证码或 API 契约变化才打断进程，请求用户的帮助。

#### 支持多Google Profile、多小红书账号并行
每个 --account 独立保存 Cookie、token/index cache、风险状态和运行锁。
同一账号仍强制单进程；不同账号可以各运行一个进程。
每个账号拥有独立断点目录：
.xhs-scrape-runs/accounts/<account>/<run-key>/

#### 限频、风控与账号安全
<table>项目
默认
单账号并发
1
搜索页间隔
3-10 秒随机
帖子详情间隔
8-24 秒随机
一级/子评论分页：随机 5–12 秒
单轮采集上限
50-100 篇帖子
连续运行时长
60 分钟后休息
异常处理
发现验证码、登录失效、页面异常时立即暂停，而不是继续重试<table>

运行保护：
- 单实例锁，避免多个任务同时操作同一个浏览器。
- 每轮任务开始前先执行登录态检查。
- 出现连续失败、验证码、登录失效、页面异常时立即暂停当前任务。
- 对失败任务记录失败原因、失败阶段、帖子 ID 或账号 ID，方便重试。
维护口径：
- 频率参数不写死在脚本里，放在环境配置中。
- 账号状态、最近运行时间、连续失败次数可查询。

## 依赖
1、xiaohongshu-CLI
2、Kimi WebBridge
  安装方法：https://www.kimi.com/zh-cn/features/webbridge
3、Google Profile（Google个人资料，无需申请新的Google账号）
目的：为了搭建多个独立的浏览器环境，用来隔离Cookie。
以抓取小红书的信息为例，设置方式如下：
  -点击 Chrome浏览器右上角头像。
  -选择“添加 Chrome 个人资料”。
  -选择“不登录账号继续”。
  -若有多个小红书账号，分别命名为 小红书-A、小红书-B
  -每个 Profile 分别扫码登录对应的小红书创作者账号。
  -为每个 Profile 分别安装并启用 Kimi WebBridge 扩展，
  -在小红书-A、小红书-B的Google浏览器中打开 Kimi WebBridge。
连续点击左上角黑色 Kimi 图标 5 次后，点击“高级设置”，
将“Daemon WebSocket 地址”分别改为：
ws://127.0.0.1:10086/ws（默认，一般无需更改）、ws://127.0.0.1:10087/ws
  - 让Agent为10086、10087配置多实例用户级常驻服务。

## 边界
抓取阶段：不使用任何浏览器，直接调用小红书签名 API。
登录、Cookie 导入、验证码处理：只使用 Kimi WebBridge。
Kimi WebBridge 控制的是你已经打开的真实、有界面的 Chrome。
CLI 不启动 Playwright、Camoufox、Selenium，也不启动其他无头或有头浏览器。




