#!/bin/bash
# Скрипт запуска Prov-GigaPath через ClearML с Docker
#
# Механизм:
#   clearml-task клонирует git репозиторий
#   Запускает docker_entrypoint.sh в контейнере
#   docker_entrypoint.sh: apt + pip deps + full_pipeline.py
#
# ВАЖНО: --requirements НЕ используется, т.к. нужен openslide
#         ДО pip установки openslide-python

PROJECT="pershin-medailab"
NAME="Pathomorphology"
QUEUE="default"
DOCKER_IMAGE="pytorch/pytorch:2.8.0-cuda12.9-cudnn9-runtime"

MAX_SLIDES=""
GPU_FLAG=""
DRY_RUN=false
SCRIPT_ARGS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --project) PROJECT="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --queue) QUEUE="$2"; shift 2 ;;
        --max_slides) MAX_SLIDES="$2"; shift 2 ;;
        --gpu) GPU_FLAG="--gpu"; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        --slide) SCRIPT_ARGS="$SCRIPT_ARGS --slide $2"; shift 2 ;;
        --level) SCRIPT_ARGS="$SCRIPT_ARGS --level $2"; shift 2 ;;
        --tile_size) SCRIPT_ARGS="$SCRIPT_ARGS --tile_size $2"; shift 2 ;;
        --batch_size) SCRIPT_ARGS="$SCRIPT_ARGS --batch_size $2"; shift 2 ;;
        --skip_tiling) SCRIPT_ARGS="$SCRIPT_ARGS --skip_tiling"; shift ;;
        --skip_annotations) SCRIPT_ARGS="$SCRIPT_ARGS --skip_annotations"; shift ;;
        *) echo "Неизвестный параметр: $1"; exit 1 ;;
    esac
done

FULL_ARGS="$GPU_FLAG"
[ -n "$MAX_SLIDES" ] && FULL_ARGS="$FULL_ARGS --max_slides $MAX_SLIDES"
FULL_ARGS="$FULL_ARGS $SCRIPT_ARGS"
FULL_ARGS=$(echo "$FULL_ARGS" | xargs)

# Загружаем HF_TOKEN из .env если есть
if [ -z "$HF_TOKEN" ] && [ -f ".env" ]; then
    export HF_TOKEN=$(grep HF_TOKEN .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
fi

# ============================================================
# Команда clearml-task
# ============================================================
CMD="clearml-task"
CMD="$CMD --project \"$PROJECT\""
CMD="$CMD --name \"$NAME\""
CMD="$CMD --queue \"$QUEUE\""
CMD="$CMD --script docker_entrypoint.sh"
CMD="$CMD --docker \"$DOCKER_IMAGE\""
# --requirements НЕ используется — openslide ставится в entrypoint ДО pip install

if [ -n "$FULL_ARGS" ]; then
    CMD="$CMD --args $FULL_ARGS"
fi

if [ "$DRY_RUN" = true ]; then
    echo "============================================"
    echo "DRY RUN — команда не будет выполнена"
    echo "============================================"
    echo ""
    echo "Команда:"
    echo "  $CMD"
    echo ""
    echo "Параметры:"
    echo "  Проект:     $PROJECT"
    echo "  Задача:     $NAME"
    echo "  Очередь:    $QUEUE"
    echo "  Docker:     $DOCKER_IMAGE"
    echo "  Скрипт:     docker_entrypoint.sh"
    echo "  Аргументы:  ${FULL_ARGS:-(нет)}"
    echo ""
    echo "Ход на агенте:"
    echo "  1. Клонирование git репозитория"
    echo "  2. Запуск Docker: $DOCKER_IMAGE"
    echo "  3. docker_entrypoint.sh:"
    echo "     a. apt-get install libopenslide0"
    echo "     b. pip install clearml[s3] boto3 openslide-python timm ..."
    echo "     c. python full_pipeline.py $FULL_ARGS"
    echo ""
    if [ -z "$HF_TOKEN" ]; then
        echo "ВНИМАНИЕ: HF_TOKEN не установлен!"
        echo "  export HF_TOKEN=your_token"
        echo "  https://huggingface.co/settings/tokens"
    else
        echo "HF_TOKEN: установлен"
    fi
else
    echo "============================================"
    echo "Запуск ClearML задачи"
    echo "============================================"
    echo "Проект:     $PROJECT"
    echo "Задача:     $NAME"
    echo "Очередь:    $QUEUE"
    echo "Docker:     $DOCKER_IMAGE"
    echo "Аргументы:  ${FULL_ARGS:-(нет)}"
    echo "============================================"
    echo ""
    if [ -z "$HF_TOKEN" ]; then
        echo "ВНИМАНИЕ: HF_TOKEN не установлен!"
        echo "Продолжить? (y/n): "
        read -r -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
    eval $CMD
fi
