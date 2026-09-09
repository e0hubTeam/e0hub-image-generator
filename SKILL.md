---
name: e0hub-image
description: 引导用户选择 E0Hub 图片模型、配置并保存 API 密钥，再根据文字描述或一张、多张参考图生成图片。适用于文生图、图生图、风格调整和多参考图合成。
---

# E0Hub Image 生图

通过对话澄清用户真正想要的画面，然后调用 `scripts/gpt_image.py` 生成并保存图片。

## 模型选择

每次开始新的生图任务，先确认用户要使用哪个模型。如果当前消息没有明确指定模型，必须先询问并等待用户选择，不要代替用户发起生成请求：

- `gpt-image-2.5-sunburst`：升级版，也是默认推荐模型
- `gpt-image-2.5-flare`：升级版
- `gpt-image-2`：经典版

用户回复“默认模型”时使用 `gpt-image-2.5-sunburst`。如果用户已经明确写出上述模型之一，视为已经完成选择，无需重复询问。后续生成命令必须通过 `--model` 传入用户选择的准确模型名。

## 密钥配置

确认模型后，在本技能目录运行：

```powershell
python scripts/gpt_image.py config-status
```

- 如果 `configured` 为 `false`，告诉用户密钥将只保存在本机技能目录，并询问 API 密钥。等用户提供后，在 PTY 中运行 `python scripts/gpt_image.py configure`，待出现输入提示后通过标准输入发送密钥。不得把真实密钥放进命令参数、命令文本、日志或回复中，也不要复述密钥。
- Windows 上密钥使用当前账户的 DPAPI 加密，保存在技能目录的 `config.local.json`；其他系统使用权限收紧的本地配置文件。
- 如果已经配置，不重复索取密钥。用户要求更换密钥时重新运行 `configure`；要求删除时运行 `clear-key`。
- 不读取或展示 `config.local.json` 的密钥字段。只用 `config-status` 检查状态。

## 引导用户

如果用户还没有给出足够信息，用简短问题帮助其明确以下内容，但只询问会实际改变结果的事项：

- 主体、场景和用途
- 画面风格、构图、光线或文字内容
- 方图、横图或竖图
- 是否有参考图片，以及每张图要参考什么
- 需要生成几张

不要让用户先理解 API 参数。未指定时使用方图 `1024x1024`、`quality=auto`、`background=auto`、PNG、1 张。把用户的自然语言整理成清晰具体的提示词，但保留其主体、风格和约束。用户已经明确要求生成时，无需再做形式化确认。

参考图必须使用实际存在的本地文件路径。多张参考图按用户给出的顺序重复传入 `--reference`。不要声称能保证像素级复刻；涉及人物、品牌或连续角色时，应说明参考图越清晰、角度越接近目标，通常越容易保持一致。

## 生成

在技能目录运行文生图：

```powershell
python scripts/gpt_image.py generate --model "用户选择的模型" --prompt "完整提示词" --size 1024x1024 --quality auto --output-format png --count 1 --output-dir "输出目录"
```

使用一张或多张参考图：

```powershell
python scripts/gpt_image.py generate --model "用户选择的模型" --prompt "完整提示词" --reference "第一张图片路径" --reference "第二张图片路径" --size 1024x1024 --quality auto --output-format png --count 1 --output-dir "输出目录"
```

把结果写入当前任务工作区的 `generated-images` 目录，不要写入技能目录。调用时始终给 `--output-dir` 传入该目录的绝对路径。脚本输出 JSON；生成成功后向用户展示绝对文件链接，并在界面支持时直接预览图片。

生成请求可能产生费用。用户明确说“生成”“制作”“出图”或同等意思即视为已授权本次请求；仅讨论提示词或方案时不要调用 API。失败后不要盲目重试，因为重复请求可能再次计费。`401` 或 `403` 时引导用户更换密钥；`429`、网络错误或服务端错误先报告，再由用户决定是否重试。
