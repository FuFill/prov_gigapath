@echo off
REM Скрипт для запуска ClearML агента на Windows
REM Использование: start_clearml_agent.bat

REM Настройки
set QUEUE_NAME=default
set GPU_DEVICES=0

echo ==========================================
echo Запуск ClearML Agent
echo ==========================================
echo Очередь: %QUEUE_NAME%
echo GPU: %GPU_DEVICES%
echo ==========================================

REM Проверка переменных окружения
if not defined CLEARML_API_ACCESS_KEY (
    echo ОШИБКА: Не установлена CLEARML_API_ACCESS_KEY
    echo Получите ключи в ClearML UI: Settings -^> Workspace -^> API Keys
    pause
    exit /b 1
)

if not defined CLEARML_API_SECRET_KEY (
    echo ОШИБКА: Не установлена CLEARML_API_SECRET_KEY
    pause
    exit /b 1
)

REM Создаем конфигурацию
echo Создание конфигурации...

(
echo api {
echo     web_server: "https://clearml.blackhole2.ai.innopolis.university"
echo     api_server: "https://clearml.blackhole2.ai.innopolis.university"
echo     files_server: "https://clearml.blackhole2.ai.innopolis.university"
echo.
echo     credentials {
echo         "access_key" = "%CLEARML_API_ACCESS_KEY%"
echo         "secret_key" = "%CLEARML_API_SECRET_KEY%"
echo     }
echo }
echo.
echo agent {
echo     gpu_device: "%GPU_DEVICES%"
echo.
echo     cache {
echo         task_cache: "%%USERPROFILE%%/clearml_agent_cache/tasks"
echo         venvs_cache: "%%USERPROFILE%%/clearml_agent_cache/venvs"
echo         pip_download_cache: "%%USERPROFILE%%/clearml_agent_cache/pip"
echo     }
echo }
echo.
echo storage {
echo     credentials {
echo         "https://api.blackhole2.ai.innopolis.university:443" {
echo             key: "YOUR_MINIO_ACCESS_KEY"
echo             secret: "YOUR_MINIO_SECRET_KEY"
echo         }
echo     }
echo }
) > %USERPROFILE%\clearml.conf

echo Конфигурация создана: %USERPROFILE%\clearml.conf

REM Создаем директории для кэша
mkdir %USERPROFILE%\clearml_agent_cache\tasks 2>nul
mkdir %USERPROFILE%\clearml_agent_cache\venvs 2>nul
mkdir %USERPROFILE%\clearml_agent_cache\pip 2>nul

REM Запускаем агента
echo Запуск агента...
clearml-agent daemon --queue %QUEUE_NAME% --gpus %GPU_DEVICES% --foreground

pause
