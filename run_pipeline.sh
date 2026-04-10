#!/bin/bash
# Скрипт для запуска полного пайплайна Prov-GigaPath в ClearML
# Использование: ./run_pipeline.sh [options]

# Параметры по умолчанию
LOCAL=false
GPU=false
MAX_SLIDES=""
SLIDES=""
LEVEL=1
TILE_SIZE=256
BATCH_SIZE=64
SKIP_TILING=false
SKIP_ANNOTATIONS=false

# Парсинг аргументов
while [[ $# -gt 0 ]]; do
    case $1 in
        --local)
            LOCAL=true
            shift
            ;;
        --gpu)
            GPU=true
            shift
            ;;
        --max_slides)
            MAX_SLIDES="$2"
            shift 2
            ;;
        --slides)
            SLIDES="$2"
            shift 2
            ;;
        --level)
            LEVEL="$2"
            shift 2
            ;;
        --tile_size)
            TILE_SIZE="$2"
            shift 2
            ;;
        --batch_size)
            BATCH_SIZE="$2"
            shift 2
            ;;
        --skip_tiling)
            SKIP_TILING=true
            shift
            ;;
        --skip_annotations)
            SKIP_ANNOTATIONS=true
            shift
            ;;
        *)
            echo "Неизвестный параметр: $1"
            exit 1
            ;;
    esac
done

# Формируем команду
CMD="python full_pipeline.py"

if [ "$LOCAL" = true ]; then
    CMD="$CMD --local"
fi

if [ "$GPU" = true ]; then
    CMD="$CMD --gpu"
fi

if [ -n "$MAX_SLIDES" ]; then
    CMD="$CMD --max_slides $MAX_SLIDES"
fi

if [ -n "$SLIDES" ]; then
    CMD="$CMD --slide $SLIDES"
fi

CMD="$CMD --level $LEVEL"
CMD="$CMD --tile_size $TILE_SIZE"
CMD="$CMD --batch_size $BATCH_SIZE"

if [ "$SKIP_TILING" = true ]; then
    CMD="$CMD --skip_tiling"
fi

if [ "$SKIP_ANNOTATIONS" = true ]; then
    CMD="$CMD --skip_annotations"
fi

# Проверка HF_TOKEN
if [ -z "$HF_TOKEN" ]; then
    echo "ВНИМАНИЕ: HF_TOKEN не установлен!"
    echo "Prov-GigaPath требует HuggingFace token для загрузки модели."
    echo "Получите токен: https://huggingface.co/settings/tokens"
    echo ""
    read -p "Продолжить без HF_TOKEN? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "=========================================="
echo "Prov-GigaPath Pipeline"
echo "=========================================="
echo "Режим: $([ "$LOCAL" = true ] && echo 'Локальный' || echo 'ClearML')"
echo "GPU: $GPU"
echo "Level: $LEVEL"
echo "Tile size: $TILE_SIZE"
echo "Batch size: $BATCH_SIZE"
echo "Max slides: ${MAX_SLIDES:-all}"
echo "=========================================="
echo ""
echo "Команда: $CMD"
echo ""

# Запуск
$CMD
