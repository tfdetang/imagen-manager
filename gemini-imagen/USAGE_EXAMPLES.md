# Gemini Imagen 使用示例

## Cookies 持久化工作流程

### 首次使用

```bash
# 第一次使用时需要提供 cookies 文件
python3 gemini_imagen.py \
  --cookies ~/Downloads/gemini_cookies.json \
  --prompt "一只可爱的橘猫" \
  --output ~/cat.png

# 输出示例：
# Loading cookies from /home/drake/Downloads/gemini_cookies.json...
# Converted 45 Google cookies
# Cookies saved to store: /home/drake/.openclaw/workspace/skills/gemini-imagen/data/cookies.json
# Navigating to Gemini...
# Logged in!
# ...
```

✅ **首次使用后，cookies 已自动保存到 `data/cookies.json`**

---

### 后续使用（无需 cookies 参数）

```bash
# 之后使用时不需要再提供 --cookies
python3 gemini_imagen.py \
  --prompt "赛博朋克风格的城市夜景" \
  --output ~/cyberpunk.png

# 输出示例：
# No cookies file specified, checking store: data/cookies.json
# Loaded cookies from store (saved: 2026-02-10T11:30:00+0800)
# Converted 45 Google cookies
# Navigating to Gemini...
# Logged in!
# ...
```

✅ **自动从 `data/cookies.json` 加载，无需手动管理 cookies！**

---

### Cookies 过期场景

当 cookies 过期时，脚本会自动检测并提示：

```bash
python3 gemini_imagen.py --prompt "测试"

# 输出：
# No cookies file specified, checking store: data/cookies.json
# Loaded cookies from store (saved: 2026-01-15T10:00:00+0800)
# Converted 45 Google cookies
# Navigating to Gemini...
# ERROR: Not logged in! Cookies may be expired.
# Please provide fresh cookies with --cookies <file>
# Removing expired cookies store: data/cookies.json
```

**解决方法：**

1. 重新导出浏览器 cookies
2. 提供新 cookies，脚本会自动保存：

```bash
python3 gemini_imagen.py \
  --cookies ~/fresh_cookies.json \
  --prompt "测试新 cookies"
  
# 新 cookies 会自动保存到 data/cookies.json，覆盖旧的
```

---

## 实际应用场景

### 场景 1：日常图片生成

```bash
# 第一次：提供 cookies
python3 gemini_imagen.py \
  --cookies ~/cookies.json \
  --prompt "夕阳下的富士山" \
  --output day1.png

# 第二天：直接使用
python3 gemini_imagen.py --prompt "樱花盛开的京都" -o day2.png

# 第三天：直接使用
python3 gemini_imagen.py --prompt "雨后的东京街道" -o day3.png
```

### 场景 2：批量图片生成

创建脚本 `batch_generate.sh`：

```bash
#!/bin/bash

# 首次运行时需要 cookies，后续批量生成无需重复提供
prompts=(
  "可爱的小狗"
  "优雅的小猫"
  "飞翔的小鸟"
  "奔跑的小马"
)

for i in "${!prompts[@]}"; do
  python3 gemini_imagen.py \
    --prompt "${prompts[$i]}" \
    --output "animal_$i.png" \
    --timeout 90
  sleep 5
done
```

### 场景 3：图片编辑工作流

```bash
# 第一步：生成初始图片（首次需要 cookies）
python3 gemini_imagen.py \
  --cookies ~/cookies.json \
  --prompt "一只白色的小猫坐在草地上" \
  --output cat_v1.png

# 第二步：基于生成的图片进行编辑（自动加载 cookies）
python3 gemini_imagen.py \
  --image cat_v1.png \
  --prompt "让小猫站起来" \
  --output cat_v2.png

# 第三步：继续编辑
python3 gemini_imagen.py \
  --image cat_v2.png \
  --prompt "添加蝴蝶在小猫身边" \
  --output cat_v3.png
```

---

## 高级用法

### 强制更新 cookies

即使已有存储的 cookies，也可以强制保存新的：

```bash
python3 gemini_imagen.py \
  --cookies ~/new_account_cookies.json \
  --save-cookies \
  --prompt "使用新账号生成"
```

### 自定义 cookies 存储位置

适用于多账号场景：

```bash
# 账号 A
python3 gemini_imagen.py \
  --cookies ~/account_a_cookies.json \
  --cookies-store ~/cookies_a.json \
  --prompt "账号 A 的图片"

# 账号 B
python3 gemini_imagen.py \
  --cookies ~/account_b_cookies.json \
  --cookies-store ~/cookies_b.json \
  --prompt "账号 B 的图片"
```

### 检查当前 cookies 状态

```bash
# 查看存储的 cookies 信息
cat data/cookies.json | jq '.saved_at, .source'

# 输出：
# "2026-02-10T11:30:00+0800"
# "/home/drake/Downloads/gemini_cookies.json"
```

---

## 故障排查

### 问题：cookies 文件不存在

```bash
$ python3 gemini_imagen.py --prompt "测试"
ERROR: No cookies available!
Please provide cookies with --cookies <file>
They will be saved automatically for future use.
```

**解决：** 提供 cookies 文件进行首次初始化

---

### 问题：所有 cookies 已过期

```bash
$ python3 gemini_imagen.py --prompt "测试"
WARNING: All stored cookies appear expired (saved: 2026-01-01T00:00:00+0800)
Please provide fresh cookies with --cookies
ERROR: No cookies available!
...
```

**解决：** 重新导出并提供新的 cookies 文件

---

### 问题：data 目录权限错误

```bash
PermissionError: [Errno 13] Permission denied: 'data/cookies.json'
```

**解决：** 确保 data 目录有写入权限：

```bash
chmod 755 data
chmod 644 data/cookies.json  # 如果文件已存在
```

---

## 总结

✅ **首次使用**：`--cookies` 必需，自动保存到 `data/cookies.json`  
✅ **后续使用**：自动从 `data/cookies.json` 加载，无需 `--cookies`  
✅ **过期处理**：自动检测，提示重新提供，自动替换旧 cookies  
✅ **多账号**：使用 `--cookies-store` 指定不同存储路径  

**一次配置，长期使用！** 🎉
