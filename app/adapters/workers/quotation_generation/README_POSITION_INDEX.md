# SpecificationMapping 位置索引功能说明

## 概述

本次更新为 [`SpecificationMapping.py`](SpecificationMapping.py) 增加了基于位置索引的字段访问功能，提高了代码的普适性和可维护性。

## 主要改进

### 1. 支持位置索引访问

除了传统的字段名访问方式，现在支持通过位置索引访问字段：

**传统方式（依赖字段名）：**
```python
{"source": "spec.25_common_bed.value", "transform": ["map_value"], "mapping": {...}}
```

**位置索引方式（推荐）：**
```python
{"source": "spec[27].value", "transform": ["map_value"], "mapping": {...}}
# 或者
{"source": "spec@27.value", "transform": ["map_value"], "mapping": {...}}
```

### 2. 位置索引的优势

- **不依赖具体字段名称**：即使字段名变化，只要位置不变，配置仍然有效
- **更具普适性**：适合处理动态生成或格式不固定的数据
- **易于维护**：减少因字段名变更导致的配置更新工作

### 3. 新增辅助方法

#### `get_spec_index_mapping()`
获取spec字段的位置索引映射表：
```python
mapping = SpecificationMapping(json_data)
index_map = mapping.get_spec_index_mapping()
# 返回: {0: "0_power_supply_v", 1: "1_power_supply_hz", ...}
```

#### `print_spec_index_mapping()`
打印spec字段的位置索引映射表，方便配置时查看：
```python
mapping = SpecificationMapping(json_data)
mapping.print_spec_index_mapping()
```

输出示例：
```
============================================================
Spec字段位置索引映射表
============================================================
[ 0] 0_power_supply_v
[ 1] 1_power_supply_hz
[ 2] 2_surface
...
============================================================
总计: 37 个字段

使用方式:
  字段名访问: {"source": "spec.2_surface.value", ...}
  位置索引访问: {"source": "spec[2].value", ...}  # 推荐
============================================================
```

## 使用方法

### 步骤1：查看字段位置映射

运行 [`SpecificationMapping.py`](SpecificationMapping.py) 或使用 [`print_spec_index_mapping()`](SpecificationMapping.py) 方法查看字段位置：

```bash
cd app/integrations/Quotation_Generation
python SpecificationMapping.py
```

### 步骤2：配置规则

根据位置索引配置 OUTPUT_RULES：

```python
OUTPUT_RULES = [
    {
        "name": "机架",
        "template": "机架（{parts}）",
        "parts": [
            {"source": "meta.model", "transform": ["extract_model_number", "normalize_model"]},
            {"source": "spec[27].value", "transform": ["map_value"], "mapping": {"PAINTED ON SS": "SS"}},
            {"source": "spec[15].value", "transform": ["extract_degree"]}
        ]
    }
]
```

### 步骤3：使用配置

```python
from SpecificationMapping import SpecificationMapping

# 使用自定义配置
mapping = SpecificationMapping(json_data, output_rules=OUTPUT_RULES)
output = mapping.generate_output_mapping()
```

## 配置示例

完整的基于位置索引的配置示例请参考：[`config_example_position_based.py`](config_example_position_based.py)

## 兼容性

- **向后兼容**：原有的字段名访问方式仍然支持
- **混合使用**：可以在同一配置中混合使用字段名和位置索引两种方式
- **无需修改现有代码**：现有配置无需修改即可继续使用

## 语法说明

### 位置索引语法

支持两种位置索引语法：

1. **方括号语法**：`spec[index].value`
   ```python
   {"source": "spec[2].value"}
   ```

2. **@符号语法**：`spec@index.value`
   ```python
   {"source": "spec@2.value"}
   ```

两种语法功能完全相同，可根据个人喜好选择。

### 索引规则

- 索引从 **0** 开始计数
- 索引对应字典中键值对的顺序
- 超出范围的索引返回 None

## 测试验证

运行测试以验证功能：

```bash
cd app/integrations/Quotation_Generation
python SpecificationMapping.py
```

测试输出将展示：
1. Spec字段位置索引映射表
2. 位置索引访问测试结果
3. 生成的元组格式输出
4. 配置建议

## 注意事项

1. **位置稳定性**：使用位置索引时，确保数据源的字段顺序保持稳定
2. **索引验证**：配置前先运行 [`print_spec_index_mapping()`](SpecificationMapping.py) 确认位置
3. **文档更新**：如果数据结构变化，及时更新位置索引映射文档

## 迁移指南

如需将现有配置迁移到位置索引方式：

1. 运行 [`print_spec_index_mapping()`](SpecificationMapping.py) 查看当前映射
2. 找到字段名对应的位置索引
3. 替换配置中的字段名为位置索引
4. 测试验证配置正确性

示例：
```python
# 迁移前
{"source": "spec.25_common_bed.value"}

# 查看映射，发现 25_common_bed 在位置 27

# 迁移后
{"source": "spec[27].value"}
```

## 更新日志

- **2026-02-01**：增加位置索引访问功能
  - 新增 [`_get_value()`](SpecificationMapping.py) 方法支持位置索引
  - 新增 [`get_spec_index_mapping()`](SpecificationMapping.py) 方法
  - 新增 [`print_spec_index_mapping()`](SpecificationMapping.py) 方法
  - 更新文档和示例代码
  - 创建 [`config_example_position_based.py`](config_example_position_based.py) 配置示例
