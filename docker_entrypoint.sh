#!/bin/bash
# Entrypoint для Docker контейнера Prov-GigaPath.
# clearml-task запускает этот скрипт, передавая $@ из --args.
#
# Этот скрипт:
#   1. Устанавливает HF_TOKEN для загрузки модели
#   2. Устанавливает libopenslide0 (apt) — нужно для openslide-python
#   3. Устанавливает pip зависимости
#   4. Запускает full_pipeline.py с теми же аргументами

set -e

echo "=========================================="
echo "Prov-GigaPath Docker Entrypoint"
echo "=========================================="
echo "Аргументы: $@"
echo ""

# ============================================================
# 0. Устанавливаем HF_TOKEN
# ============================================================
if [ -n "$HF_TOKEN" ]; then
    echo "[0/4] HF_TOKEN установлен (из окружения)"
    export HF_TOKEN="$HF_TOKEN"
else
    # Если не передан через окружение, пробуем загрузить из .env
    if [ -f ".env" ]; then
        echo "[0/4] Загрузка HF_TOKEN из .env..."
        export HF_TOKEN=$(grep HF_TOKEN .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
        echo "  OK (из .env)"
    else
        echo "[0/4] ВНИМАНИЕ: HF_TOKEN не установлен!"
        echo "  Загрузка модели Prov-GigaPath может завершиться ошибкой."
    fi
fi

# ============================================================
# 1. Устанавливаем libopenslide0 (системная библиотека для openslide-python)
# ============================================================
if ! ldconfig -p 2>/dev/null | grep -q libopenslide; then
    echo "[1/4] Установка libopenslide0..."
    apt-get update -qq 2>/dev/null
    apt-get install -y -qq libopenslide0 2>/dev/null
    echo "  OK"
else
    echo "[1/4] libopenslide0 уже установлен"
fi

# ============================================================
# 2. Устанавливаем pip зависимости
# ============================================================
echo "[2/4] Установка pip зависимостей..."

# Проверяем есть ли requirements_clearml.txt в текущей директории
# (clearml-task клонирует репозиторий и запускает из его корня)
if [ -f "requirements_clearml.txt" ]; then
    pip install --quiet -r requirements_clearml.txt 2>&1 | tail -5
else
    echo "  [WARN] requirements_clearml.txt не найден, устанавливаю напрямую..."
    pip install --quiet clearml[s3] boto3 openslide-python timm huggingface-hub h5py matplotlib pandas pillow tqdm einops monai scikit-image scikit-learn torchmetrics fvcore iopath transformers omegaconf lifelines scikit-survival fairscale tensorboard 2>&1 | tail -5
fi

echo "  OK"

# ============================================================
# 3. Дополнительные зависимости (flash_attn/xformers — опционально)
# ============================================================
echo "[3/4] Пропускаю опциональные зависимости..."
echo "  flash_attn/xformers не установлены (не нужны для inference)"

# ============================================================
# 4. Запускаем full_pipeline.py
# ============================================================
echo "[4/4] Запуск full_pipeline.py..."
echo "=========================================="

# Передаём все аргументы скрипту
exec python full_pipeline.py "$@"
