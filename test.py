# 简单的测试脚本 - 完全独立版本
import random

# 定义必要的常量
GRID_WIDTH = 40
GRID_HEIGHT = 30
UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)

# 辅助函数
def add_positions(pos1, pos2):
    return (pos1[0] + pos2[0], pos1[1] + pos2[1])

def is_valid_position(position):
    x, y = position
    return 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT

def get_opposite_direction(direction):
    opposites = {
        UP: DOWN,
        DOWN: UP,
        LEFT: RIGHT,
        RIGHT: LEFT
    }
    return opposites.get(direction, (0, 0))

def random_position():
    x = random.randint(0, GRID_WIDTH - 1)
    y = random.randint(0, GRID_HEIGHT - 1)
    return (x, y)


class SimpleSnake:
    """简化的蛇类用于测试"""
    def __init__(self):
        self.body = [(GRID_WIDTH // 2, GRID_HEIGHT // 2)]
        self.direction = RIGHT
        self.grow_next = False

    def move(self):
        head = self.body[0]
        new_head = add_positions(head, self.direction)

        if not is_valid_position(new_head):
            return False

        if new_head in self.body:
            return False

        self.body.insert(0, new_head)

        if not self.grow_next:
            self.body.pop()
        else:
            self.grow_next = False

        return True

    def change_direction(self, new_direction):
        if new_direction != get_opposite_direction(self.direction):
            self.direction = new_direction

    def get_head(self):
        return self.body[0]

    def get_body(self):
        return self.body.copy()

    def get_length(self):
        return len(self.body)


def test_snake():
    """测试蛇的基本功能"""
    snake = SimpleSnake()
    print("初始蛇身长度:", snake.get_length())
    print("初始蛇头位置:", snake.get_head())

    # 测试移动
    snake.move()
    print("移动后蛇头位置:", snake.get_head())

    # 测试改变方向
    snake.change_direction(DOWN)
    snake.move()
    print("向下移动后蛇头位置:", snake.get_head())

    print("蛇测试完成!")


class SimpleFood:
    """简化的食物类用于测试"""
    def __init__(self, snake_body=None):
        self.position = self.generate_position(snake_body or [])

    def generate_position(self, snake_body):
        while True:
            position = random_position()
            if position not in snake_body:
                return position

    def respawn(self, snake_body):
        self.position = self.generate_position(snake_body)

    def get_position(self):
        return self.position


def test_food():
    """测试食物功能"""
    food = SimpleFood()
    print("食物位置:", food.get_position())

    # 测试重新生成
    food.respawn([(5, 5), (6, 5)])
    print("重新生成后食物位置:", food.get_position())

    print("食物测试完成!")


if __name__ == "__main__":
    print("开始测试游戏组件...")
    test_snake()
    print()
    test_food()
    print("所有测试完成!")
