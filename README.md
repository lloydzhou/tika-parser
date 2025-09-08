# tika-parser
高性能基于 Tika 的 HTML -> Markdown 转换服务（使用 lxml，图片以 base64 内联）。

主要功能
- 接受 multipart/form-data 上传的文件（表单字段名为 `file`），直接调用外部 Tika 服务解析。
- 返回 Markdown 文本（Content-Type: text/markdown; charset=utf-8）。
- 所有可用的内嵌图片会被转换为 data URI（data:image/...;base64,...）并内联到 Markdown 中。

快速开始（使用 Docker 镜像）

1) 直接运行预构建镜像（推荐）

```bash
docker run --rm -p 8888:8888 -p 9998:9998 -e lloydzhou/tika-parser:markdown-tika-lxml
```

2) 发送文件并获取 Markdown

```bash
curl -sS -X POST "http://127.0.0.1:8888/" -F "file=@/path/to/your/document.pdf" -o output.md
```

响应
- 成功时返回纯 Markdown 文本，HTTP 状态 200。图片会以 data URI 的形式内联，例如：
```
  ![alt text](data:image/png;base64,iVBORw0KGgoAAAANS...)
```
- 错误时返回相应的 HTTP 错误码与简短描述（例如 400 空文件，502 Tika 服务错误等）。

本地开发

```bash
# 推荐在虚拟环境中运行
pip install -r requirements.txt
python main.py
```

调试与注意事项
- 服务将调用外部 Tika `/rmeta` 和 `/unpack/all` 等端点以获取主文档 HTML 与嵌入资源。
- 对于非常大的文档，解析器启用了容错与大树支持（recover=True, huge_tree=True），但仍建议在 Tika 端合理配置内存与超时。
- 如果你需要仅查看解析后 HTML 的 img 数量或局部内容，可在日志中开启 DEBUG 级别以便排查解析差异。

镜像
- 可以直接使用镜像： `lloydzhou/tika-parser:markdown-tika-lxml`。

许可证
- 项目遵循仓库根目录中的 LICENSE。


