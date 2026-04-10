# Prov-GigaPath в ClearML — Полное руководство

## Обзор пайплайна

Пайплайн Prov-GigaPath включает следующие этапы:

1. **Загрузка данных из MinIO** — WSI слайды (.tif, .ndpi, .svs) и XML аннотации
2. **Тайлинг слайдов** — разбиение гигапиксельных слайдов на тайлы 256x256
3. **Tile Encoder** — извлечение фич из каждого тайла через GigaPath tile encoder
4. **Slide Encoder** — агрегация тайлов в эмбеддинги всего слайда
5. **Разметка лимфом** — визуальная пометка тайлов, содержащих лимфомы (по XML аннотациям)

## Структура данных в MinIO

Данные хранятся в бакете `pershin-medailab` по следующему пути:

```
Pathomorphology/CAMELYON/
├── 16/
│   ├── training/
│   │   ├── tumor/
│   │   │   ├── tumor_001.tif
│   │   │   ├── tumor_002.tif
│   │   │   └── ...
│   │   ├── normal/
│   │   │   ├── normal_001.tif
│   │   │   └── ...
│   │   └── annotations/
│   │       ├── tumor_001.xml
│   │       └── ...
│   └── testing/
│       ├── test_001.tif
│       ├── test_002.tif
│       └── ...
│       └── annotations/
│           └── ...
└── 17/
    └── ...
```

## Предварительные требования

### 1. Установка зависимостей

```bash
# Клонируем репозиторий
git clone https://github.com/prov-gigapath/prov-gigapath
cd prov-gigapath

# Создаем окружение
conda env create -f environment.yaml
conda activate gigapath

# Устанавливаем пакет
pip install -e .

# Дополнительные зависимости
pip install clearml boto3 h5py
```

### 2. Установка OpenSlide и библиотек для тайлинга

**Windows:**
```bash
# Скачайте и установите OpenSlide с https://openslide.org/download/
# Добавьте путь к bin директории OpenSlide в PATH
```

**Linux:**
```bash
sudo apt-get install openslide-tools libopenslide0
```

**Важно:** Проверьте версию pixman (не используйте 0.38):
```bash
ldd $(which ls) | grep pixman
# Должно быть 0.40.0 или новее
```

### 3. Настройка ClearML

```bash
# Установка clearml-agent (на сервере с GPU)
pip install clearml-agent

# Инициализация конфигурации
clearml-init
```

Вам понадобятся:
- **API Access Key** (из ClearML сервера)
- **API Secret** (из ClearML сервера)
- **Web Server URL** (например, `https://clearml.your-domain.com`)

### 4. Настройка HuggingFace Token

Prov-GigaPath требует токен для загрузки весов модели:

```bash
export HF_TOKEN=your_huggingface_token
```

Получить токен: https://huggingface.co/settings/tokens

## Запуск пайплайна

### Вариант 1: Локальный запуск (для тестирования)

```bash
# Один слайд (тест)
python full_pipeline.py --local --max_slides 1 --gpu

# Все слайды из MinIO
python full_pipeline.py --local --gpu

# Конкретные слайды
python full_pipeline.py --local --slide Pathomorphology/CAMELYON/16/training/tumor/tumor_001.tif --gpu

# Пропустить тайлинг (если тайлы уже есть)
python full_pipeline.py --local --skip_tiling --gpu

# Пропустить разметку аннотаций
python full_pipeline.py --local --skip_annotations --gpu
```

### Вариант 2: Запуск через ClearML (рекомендуется)

#### Шаг 1: Задача в ClearML

```bash
# Запуск задачи в ClearML (задача создастся и отправится в очередь)
python full_pipeline.py --gpu --max_slides 5
```

Это создаст задачу в ClearML с названием `Prov-GigaPath Full Pipeline` в проекте `Prov-GigaPath/CAMELYON`.

#### Шаг 2: Настройка ClearML Agent

На сервере с GPU (например, ваша VM):

```bash
# Запуск агента (слушает очередь default)
clearml-agent daemon --queue default --gpus 0 --docker

# Или без Docker
clearml-agent daemon --queue default --gpus 0
```

Агент автоматически:
1. Заберет задачу из очереди
2. Клонирует репозиторий
3. Установит зависимости
4. Выполнит пайплайн
5. Сохранит результаты в артефактах

#### Шаг 3: Мониторинг в ClearML UI

Откройте ClearML Web UI и наблюдайте за:
- **Progress** — прогресс выполнения
- **Console** — логи выполнения
- **Artifacts** — результаты (эмбеддинги, метаданные)
- **Plots** — метрики и графики

## Параметры пайплайна

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `--level` | Magnification level (0 = highest) | 1 |
| `--tile_size` | Размер тайла в пикселях | 256 |
| `--batch_size` | Batch size для tile encoder | 64 |
| `--gpu` | Использовать GPU | False |
| `--max_slides` | Лимит слайдов (для теста) | None |
| `--skip_tiling` | Пропустить тайлинг | False |
| `--skip_annotations` | Пропустить разметку лимфом | False |
| `--slide` | Конкретные слайды для обработки | None |

## Результаты

После выполнения пайплайна в директории `outputs/` для каждого слайда будут:

```
outputs/clearml_pipeline/
└── tumor_001/
    ├── tumor_001.tif                          # Скачанный слайд
    ├── tiles/                                 # Тайлы
    │   ├── 000000x_000000y.png
    │   └── ...
    ├── tumor_001_tile_embeddings.pt           # Эмбеддинги тайлов
    ├── tumor_001_slide_embeddings.h5          # Эмбеддинги слайда
    ├── tumor_001_annotation.xml               # Аннотации
    ├── marked_tiles/                          # Помеченные тайлы
    │   ├── 000256x_000256y.png  (с красной рамкой = лимфома)
    │   └── ...
    └── tiles_metadata.json                    # Метаданные тайлов
```

### tiles_metadata.json

Содержит информацию о каждом тайле:
```json
[
  {
    "tile_path": "...",
    "tile_x": 256,
    "tile_y": 512,
    "has_lymphoma": true
  },
  ...
]
```

## Архитектура модели

### Tile Encoder
- **Модель**: Prov-GigaPath (ViT)
- **Вход**: 224x224 изображения тайлов
- **Выход**: 1536-мерные эмбеддинги

### Slide Encoder
- **Модель**: LongNet Vision Transformer
- **Вход**: N тайловых эмбеддингов + координаты
- **Выход**: 768-мерные эмбеддинги слайда (на разных слоях)

## Проверка позиций тайлов

**Важно:** Всегда проверяйте корректность позиций тайлов!

### Как проверить:

1. **Визуальная проверка:**
   - Откройте `marked_tiles/` — тайлы с красной рамкой должны содержать лимфомы
   - Сравните с оригинальным слайдом в viewer (например, [QuPath](https://qupath.github.io/))

2. **Координаты в метаданных:**
   ```bash
   cat outputs/clearml_pipeline/tumor_001/tiles_metadata.json | head -50
   ```

3. **Сравнение с аннотациями:**
   - Откройте XML аннотацию
   - Проверьте, что координаты тайлов попадают в полигоны аннотаций

### Если позиции не совпадают:

Сообщите мне, указав:
- Название слайда
- Пример тайла (имя файла)
- Ожидаемые vs фактические координаты

## Troubleshooting

### Ошибка: "pixman 0.38 has a known issue"

```bash
# Проверка версии
ldd $(which ls) | grep pixman

# Если 0.38 — обновите до 0.40.0
```

### Ошибка: "CUDA out of memory"

```bash
# Уменьшите batch size
python full_pipeline.py --local --batch_size 32 --gpu
```

### Ошибка: "HF_TOKEN not set"

```bash
export HF_TOKEN=your_token
# Или добавьте в ~/.bashrc
```

### Ошибка: "MinIO connection failed"

Проверьте доступность сервера:
```bash
curl -I https://api.blackhole2.ai.innopolis.university:443
```

### ClearML agent не запускает задачи

```bash
# Проверьте конфигурацию
cat ~/clearml.conf

# Перезапустите агента
clearml-agent daemon --queue default --gpus 0 --foreground
```

## Продвинутые настройки

### Кастомная очередь ClearML

```bash
# Запуск в кастомную очередь
python full_pipeline.py --gpu
# В коде измените queue_name="your_queue"
```

### Много-GPU

```bash
# Запуск на нескольких GPU
clearml-agent daemon --queue default --gpus 0,1,2,3
```

### Docker

```bash
# Запуск в Docker
clearml-agent daemon --queue default --docker nvidia/cuda:12.0-runtime
```

## Примеры использования

### 1. Тестирование на одном слайде

```bash
python full_pipeline.py --local --max_slides 1 --level 1 --gpu
```

### 2. Обработка всех tumor слайдов

```bash
python full_pipeline.py --local \
  --slide $(aws s3 ls s3://pershin-medailab/Pathomorphology/CAMELYON/16/training/tumor/ | grep .tif | awk '{print "Pathomorphology/CAMELYON/16/training/tumor/"$4}') \
  --gpu
```

### 3. Только эмбеддинги (без разметки)

```bash
python full_pipeline.py --local --skip_annotations --gpu
```

### 4. Только разметка лимфом

```bash
python full_pipeline.py --local --skip_tiling --gpu
```

## Интеграция с MinIO (загрузка данных)

Если нужно только загрузить данные в MinIO:

```bash
# Загрузка CAMELYON16
python load_data_clearml.py --parallel 4

# Загрузка аннотаций
python load_annotations.py

# Или автономно на сервере
python load_data_server.py --parallel 4
```

## Ссылки

- [Prov-GigaPath GitHub](https://github.com/prov-gigapath/prov-gigapath)
- [GigaPath Paper](https://aka.ms/gigapath)
- [ClearML Documentation](https://clear.ml/docs)
- [CAMELYON16 Dataset](https://camelyon16.grand-challenge.org/)
