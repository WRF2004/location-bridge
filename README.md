# Location Bridge · 位置遥控

一个在 Mac 本地运行的 iPhone 模拟定位控制面板。用 Mac 连接自己的 iPhone，在电脑或手机浏览器里保存地点、点击切换，并手动开启或恢复真实定位。

**版本：0.2.0 · macOS 启动器 · MIT License**

## 功能

- 手动开关模拟定位，没有固定时长限制。
- 保存多个命名地点，一键开启或切换；支持编辑、删除。
- 地图选点，或输入 WGS84 经纬度；默认不配置任何目标地点。
- 本地生成二维码，手机扫描即可打开控制面板。
- USB 连接；完成首次配置后可尝试同一局域网无线连接。
- 正常退出时执行恢复；主程序异常退出后，独立定位子进程通过管道 EOF 尝试清理。
- API 校验访问令牌、Host 和 Origin；不提供任意命令执行接口。

工具修改的是 **iPhone 设备模拟定位**，不会改变网络 IP，也不修改 macOS 全局定位。其他应用是否使用、接受或标记模拟定位，取决于各自实现。

## 环境要求

- Mac，Python 3.9 或更新版本（推荐使用 python.org 或 Homebrew 提供的现代 OpenSSL 环境）。
- 可以解锁、信任此电脑并开启开发者模式的 iPhone。
- 首次连接需要数据线；首次安装依赖和准备开发者映像需要网络。
- 手机控制页与 Mac 位于同一可信局域网。

本项目通过 `pymobiledevice3==11.40.3` 控制 iOS 17 及更新系统的 DVT 定位服务。不同 iOS、macOS、Python 和开发者映像版本可能影响可用性；这不是所有设备组合的兼容性承诺。详见 [验证范围](VALIDATION.md)。

## 开始使用

1. 下载本仓库完整 ZIP 并解压，或克隆仓库：

   ```sh
   git clone https://github.com/WRF2004/location-bridge.git
   cd location-bridge
   ```

2. 在 Finder 中双击 **Start.command**。也可在终端运行：

   ```sh
   bash Start.command
   ```

   启动器会在用户的 Application Support 目录创建独立 Python 环境并安装依赖，然后打开本地控制页。Mac 尚未安装 Python 时，会打开 Python 官方安装页面。

3. USB 连接并解锁 iPhone，按提示“信任此电脑”。按页面顺序检查连接、显示开发者模式开关、在手机设置里开启并重启确认，再点击“准备设备”。无需关闭锁屏密码。
4. 点“添加地点”，在地图中选点或输入经纬度，核对后保存。可以继续添加多个地点。
5. 点任意地点卡片开启或切换；用完关闭模拟定位开关。

手机上操作：在 Mac 页面展开“在手机上打开”，用相机扫描二维码，在 Safari 打开。可收藏页面以便下次使用。`LocationBridge.app` 是可选的未签名启动包装，须与仓库其他文件保持在同一目录。

## 关闭与恢复

**关闭浏览器标签页不会关闭 Mac 后台，也不会关闭模拟定位。**

推荐点击页面上的“恢复并退出程序”。也可以在启动终端按 **Control+C**。正常退出会等待恢复流程；页面退出按钮在恢复失败时保留后台，便于重试。

主程序意外退出或单独被强杀时，独立子进程会尝试执行恢复命令。Mac 断电、手机断连、相关进程全部被杀或系统开发者服务异常时，无法保证恢复命令送达。重新连接后恢复，并在手机地图中核对；必要时重启 iPhone 后检查。程序不会把命令返回当作手机地图已确认恢复。

Mac 必须保持运行并与手机连接。启动器通过 `caffeinate -i` 避免空闲睡眠，但合盖、断电和网络变化仍可能中断连接。

## 地点与坐标

- 使用 WGS84 经纬度。高德/腾讯常用 GCJ-02，百度常用 BD-09，不能直接混填。
- 地图初始中心仅用于浏览，不会自动保存为目标位置。
- 地图由 OpenStreetMap 提供，加载失败时可手动填入已核实坐标。
- 编辑保存不会立即改变正在使用的坐标，再次点击卡片才应用修改。
- 当前正在使用的地点需要先关闭或切换后才能删除；最多保存 100 个地点。
- 旧版 `place.json` 自动迁移为 `places.json`，旧文件保留。

## 本地数据与安全

运行环境和个人配置位于：

```text
~/Library/Application Support/LocationBridge
```

包括 Python 虚拟环境、个人地点、随机访问令牌和未确认恢复的会话记录。这些文件不应提交到仓库。

控制页链接和二维码包含遥控密钥，请勿公开或分享。服务使用局域网 HTTP，仅适合可信网络；不要映射到公网。地图瓦片由浏览器请求 OpenStreetMap，应用不会向地图服务发送控制密钥或设备标识。

本项目不需要第三方账号、姓名、学号或其他应用的登录授权。日志仅在内存中保留最近 50 条，并尽量遮蔽设备标识。

## 常见问题

| 问题 | 处理 |
| --- | --- |
| 未发现 iPhone | 用 USB 连接、解锁并信任电脑，检查数据线；只保留一台目标设备。 |
| 没有开发者模式入口 | 点击显示开关后退出并重开手机“设置 → 隐私与安全性”，滑到底部。开启后按提示重启并确认。 |
| 准备设备失败 | 检查开发者模式、映像下载网络，并查看诊断信息。 |
| 手机打不开控制页 | 确认同一局域网及 Mac 本地网络/防火墙权限；校园网隔离时可用个人热点或私人路由器。 |
| 位置状态未知 | 接回原来的 iPhone，执行恢复并检查地图；工具不会在此时改动另一台手机。 |
| Mac 地址改变 | 重启工具并重新扫码。 |
| LibreSSL / HTTPS 错误 | 使用带现代 OpenSSL 的 Python 环境；不要禁用证书验证。 |
| macOS 阻止未签名应用 | 核对来源后使用系统提供的允许入口，或运行 Start.command；不需要关闭整体系统安全设置。 |

## 开发与测试

后端主要使用 Python 标准库，前端使用原生 HTML/CSS/JavaScript；依赖固定在 [requirements.txt](requirements.txt)。

```sh
python3 -m unittest -v test_bridge.py
node --check app.js
```

JSDOM 流程测试和贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。测试使用假设备 CLI，不能代替真机验收。

## Mac 自身的定位

本项目不包含 macOS 全局定位修改。Chrome 可以通过开发者工具的 Sensors → Custom location 模拟受调试网页的经纬度；它不会修改所有 Mac 应用的位置或 IP。参见 [Chrome 官方说明](https://developer.chrome.com/docs/devtools/sensors#geolocation)。

## 许可证与依赖

本仓库自行编写的控制器、启动器、界面和测试按 [MIT License](LICENSE) 开源。第三方组件遵循各自许可证，仓库不捆绑第三方源码、Python 环境或开发者映像。

- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3)：GPL-3.0；独立安装，通过 CLI 及其等待钩子控制会话。
- [Segno](https://github.com/heuer/segno)：BSD-3-Clause；本地生成二维码。
- [OpenStreetMap](https://www.openstreetmap.org/copyright)：地图数据 © OpenStreetMap contributors；使用公共瓦片需遵守其[瓦片使用政策](https://operations.osmfoundation.org/policies/tiles/)。
