#!/bin/bash
cd frontend
echo "🚀 启动前端测试服务器..."
echo "📍 访问地址："
echo "   - http://localhost:8888/index.html"
echo "   - http://localhost:8888/overview.html"
echo "   - http://localhost:8888/workload.html"
echo ""
echo "⚠️  注意：API 调用会失败（因为后端未启动），但可以验证前端代码是否有语法错误"
echo ""
echo "按 Ctrl+C 停止服务器"
echo ""
python3 -m http.server 8888
