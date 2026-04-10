#!/bin/bash
# Скрипт для запуска ClearML агента на сервере с GPU
# Использование: ./start_clearml_agent.sh

# Настройки
QUEUE_NAME="default"
GPU_DEVICES="0"
USE_DOCKER=false
DOCKER_IMAGE="nvidia/cuda:12.0-runtime-ubuntu20.04"

# Проверка переменных
if [ -z "$CLEARML_API_ACCESS_KEY" ] || [ -z "$CLEARML_API_SECRET_KEY" ]; then
    echo "ОШИБКА: Не установлены CLEARML_API_ACCESS_KEY и CLEARML_API_SECRET_KEY"
    echo "Получите ключи в ClearML UI: Settings -> Workspace -> API Keys"
    exit 1
fi

echo "=========================================="
echo "Запуск ClearML Agent"
echo "=========================================="
echo "Очередь: $QUEUE_NAME"
echo "GPU: $GPU_DEVICES"
echo "Docker: $USE_DOCKER"
echo "=========================================="

# Создаем конфигурацию
cat > ~/clearml.conf << EOF
api {
    web_server: "https://clearml.blackhole2.ai.innopolis.university"
    api_server: "https://clearml.blackhole2.ai.innopolis.university"
    files_server: "https://clearml.blackhole2.ai.innopolis.university"
    
    credentials {
        "access_key" = "$CLEARML_API_ACCESS_KEY"
        "secret_key" = "$CLEARML_API_SECRET_KEY"
    }
}

agent {
    gpu_device: "$GPU_DEVICES"
    
    cache {
        task_cache: "~/clearml_agent_cache/tasks"
        venvs_cache: "~/clearml_agent_cache/venvs"
        pip_download_cache: "~/clearml_agent_cache/pip"
    }
}

storage {
    credentials {
        "https://api.blackhole2.ai.innopolis.university:443" {
            key: "NQVWXgIRAIIvAM0C8DbK"
            secret: "Bd2dC49zYy5VfEiuP19s7b6VpBRz0KxClxF18cNv"
        }
    }
}
EOF

echo "Конфигурация создана: ~/clearml.conf"

# Создаем директории для кэша
mkdir -p ~/clearml_agent_cache/{tasks,venvs,pip}

# Запускаем агента
if [ "$USE_DOCKER" = true ]; then
    echo "Запуск в Docker..."
    clearml-agent daemon \
        --queue "$QUEUE_NAME" \
        --gpus "$GPU_DEVICES" \
        --docker "$DOCKER_IMAGE" \
        --foreground
else
    echo "Запуск без Docker..."
    clearml-agent daemon \
        --queue "$QUEUE_NAME" \
        --gpus "$GPU_DEVICES" \
        --foreground
fi
