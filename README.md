# 🐍 贪吃蛇游戏

一个用Python和Pygame开发的经典贪吃蛇游戏。

## 功能特性

- 🎮 经典贪吃蛇玩法
- 🖥️ 可视化图形界面
- ⌨️ 键盘控制 (方向键或WASD)
- 📊 实时分数显示
- 🚀 动态速度递增
- 🔄 游戏重新开始功能

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行游戏

```bash
python main.py
```

## 游戏控制

- **方向键** 或 **WASD**: 控制蛇的移动方向
- **R**: 游戏结束后重新开始
- **ESC**: 退出游戏

## 游戏规则

1. 使用键盘控制蛇的移动方向
2. 吃到红色食物可以增长身体并获得分数
3. 避免撞到墙壁或自己的身体
4. 随着分数增加，游戏速度会逐渐加快

## 项目结构

```
snake_game/
├── main.py              # 游戏入口
├── game.py              # 游戏主控制器
├── snake.py             # 蛇类
├── food.py              # 食物类
├── display.py           # 显示界面
├── constants.py         # 游戏常量
├── utils.py             # 辅助工具
├── requirements.txt     # 依赖包
└── README.md           # 说明文档
```

## 技术栈

- **Python 3.x**
- **Pygame** - 游戏开发框架
