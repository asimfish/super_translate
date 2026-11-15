# 原生颜色与渐变辅助库

这里保存两份 MuPDF 1.25.2 只读辅助库的 C 源码、80 份官方头文件的指纹，以及实际 PDF 回归脚本。用于防止把 x86-64 辅助库误放入 ARM 运行环境；不会安装、覆盖或发布翻译候选。

## 编译

在目标架构的 64 位 Linux 编译环境中，准备 MuPDF 1.25.2 的 `include` 目录及 C 编译器，然后运行：

```sh
python scripts/native_observers/build_native_observers.py \
  --include-dir /path/to/mupdf-1.25.2/include \
  --output-dir /tmp/native-observer-build
```

输出目录必须尚不存在。程序先核对全部头文件，再编译、核对两个 ELF 文件的实际架构，并在独立子进程中检查库版本。输出包含两份 `.so` 和记录源码、编译命令、产物指纹的 `build-report.json`。使用已有产物的冻结候选必须重新编号、重新冻结和重新验证，不能覆盖旧候选中的文件。

头文件来源为 [MuPDF 1.25.2 官方标签](https://github.com/ArtifexSoftware/mupdf/tree/1.25.2/include)。原标签归档 SHA256 为 `492a8497fb3c03b700e5d6ff95e244d1a93a98db6a85a51138226d5f03444d57`；验收运行环境构建所用的同版本发行源码归档为 `36ccf6a5e691e188acf8db6e98d08bf05f27bb4ce30432dc15fc76d329a92d4d`，其所需 80 份头文件与这里的指纹一致。

## 实际 PDF 回归

需要外部冻结运行包、与其身份一致的 Python／原生环境、正常字体缓存及原定源 PDF。脚本不调用供应商，不使用翻译缓存，也不生成验收译文。每次使用新的输出目录，在独立进程中执行：

```sh
python -B scripts/native_observers/probe_source_shade_projection.py \
  --source /path/to/s41467-024-52519-8.pdf \
  --bundle /path/to/frozen-bundle \
  --manifest-sha256 EXPECTED_MANIFEST_SHA256 \
  --font-cache-dir /path/to/normal-font-cache \
  --output-dir /tmp/source-shade-probe

python -B scripts/native_observers/probe_native_colour_profiles.py \
  --source /path/to/s41467-024-54639-7.pdf \
  --bundle /path/to/frozen-bundle \
  --manifest-sha256 EXPECTED_MANIFEST_SHA256 \
  --font-cache-dir /path/to/normal-font-cache \
  --output-dir /tmp/native-colour-probe
```

第一项验证 Nature 52519 第 6 页的真实渐变路径、未修改文档一致性、实际图像及空页面拒绝、真实渐变坐标与裁剪修改检测、安装及两次恢复。第二项通过 Nature 54639 全部 16 页的实际 MuPDF 回调读取完整 sRGB／FOGRA39 配置，并保存原生文字、路径、颜色和上下文观察供跨环境比较。两份脚本都会核对固定源文件指纹及候选清单身份。

本地回归已复现旧 ARM 包的加载错误；原 x86 包和重新编译的 ARM 探针均通过第一项 7 个控制，完整原始渐变证据一致。此结果只覆盖辅助库回归。真实整批翻译、独立 PDF 门禁、人工版式及浮窗流程仍须通过，不能用这些检查宣称 20 篇验收完成。

论文、字体、凭据、翻译缓存及生成的二进制均不在本目录中。
