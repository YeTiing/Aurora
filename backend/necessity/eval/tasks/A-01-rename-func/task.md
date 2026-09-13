# 任务：把 src/textkit/strings.py 里的 `trim_ws` 改名为 `normalize_text`

`trim_ws` 的名字只体现了「去空白」，但调用方都在按更通用的语义使用它。
请把它改名为 `normalize_text`，并同步更新仓库里所有调用它的地方。

行为不要变：输入 `"  hi  "` 仍返回 `"hi"`。
改完后旧名字不应再存在（不要保留一个别名）。
