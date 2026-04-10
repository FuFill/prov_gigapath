@echo off
REM Скрипт для запуска полного пайплайна Prov-GigaPath в ClearML (Windows)
REM Использование: run_pipeline.bat [options]

REM Параметры по умолчанию
set LOCAL=false
set GPU=false
set MAX_SLIDES=
set SLIDES=
set LEVEL=1
set TILE_SIZE=256
set BATCH_SIZE=64
set SKIP_TILING=false
set SKIP_ANNOTATIONS=false

REM Парсинг аргументов
:parse_args
if "%1"=="" goto end_parse
if "%1"=="--local" (
    set LOCAL=true
    shift
    goto parse_args
)
if "%1"=="--gpu" (
    set GPU=true
    shift
    goto parse_args
)
if "%1"=="--max_slides" (
    set MAX_SLIDES=%2
    shift
    shift
    goto parse_args
)
if "%1"=="--level" (
    set LEVEL=%2
    shift
    shift
    goto parse_args
)
if "%1"=="--tile_size" (
    set TILE_SIZE=%2
    shift
    shift
    goto parse_args
)
if "%1"=="--batch_size" (
    set BATCH_SIZE=%2
    shift
    shift
    goto parse_args
)
if "%1"=="--skip_tiling" (
    set SKIP_TILING=true
    shift
    goto parse_args
)
if "%1"=="--skip_annotations" (
    set SKIP_ANNOTATIONS=true
    shift
    goto parse_args
)
echo Неизвестный параметр: %1
exit /b 1

:end_parse

REM Формируем команду
set CMD=python full_pipeline.py

if "%LOCAL%"=="true" set CMD=%CMD% --local
if "%GPU%"=="true" set CMD=%CMD% --gpu
if not "%MAX_SLIDES%"=="" set CMD=%CMD% --max_slides %MAX_SLIDES%
if not "%SLIDES%"=="" set CMD=%CMD% --slide %SLIDES%
set CMD=%CMD% --level %LEVEL%
set CMD=%CMD% --tile_size %TILE_SIZE%
set CMD=%CMD% --batch_size %BATCH_SIZE%
if "%SKIP_TILING%"=="true" set CMD=%CMD% --skip_tiling
if "%SKIP_ANNOTATIONS%"=="true" set CMD=%CMD% --skip_annotations

REM Проверка HF_TOKEN
if not defined HF_TOKEN (
    echo ВНИМАНИЕ: HF_TOKEN не установлен!
    echo Prov-GigaPath требует HuggingFace token для загрузки модели.
    echo Получите токен: https://huggingface.co/settings/tokens
    echo.
    set /p CONTINUE="Продолжить без HF_TOKEN? (y/n): "
    if /i not "%CONTINUE%"=="y" exit /b 1
)

echo ==========================================
echo Prov-GigaPath Pipeline
echo ==========================================
if "%LOCAL%"=="true" (
    echo Режим: Локальный
) else (
    echo Режим: ClearML
)
echo GPU: %GPU%
echo Level: %LEVEL%
echo Tile size: %TILE_SIZE%
echo Batch size: %BATCH_SIZE%
echo Max slides: %MAX_SLIDES%
echo ==========================================
echo.
echo Команда: %CMD%
echo.

REM Запуск
%CMD%

pause
