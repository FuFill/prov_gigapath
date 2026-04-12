"""
Prov-GigaPath полный пайплайн в ClearML:
1. Загрузка WSI слайдов из MinIO
2. Тайлинг слайдов (разбиение на тайлы)
3. Tile encoder — получение эмбеддингов тайлов
4. Slide encoder — получение эмбеддингов слайдов
5. Визуализация и разметка лимфом (по аннотациям XML)

Запуск:
    # В ClearML (удаленно):
    python full_pipeline.py

    # Локально (для теста):
    python full_pipeline.py --local
"""

import os
import sys
import argparse
import torch
import timm
import h5py
import numpy as np
import boto3
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm
from typing import List, Tuple, Dict, Optional
import shutil

# ClearML
from clearml import Task, Logger

# Внутренние модули
from openslide import OpenSlide
from gigapath.pipeline import (
    tile_one_slide,
    load_tile_encoder_transforms,
)


# ============================================================
# Конфигурация MinIO
# ============================================================
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "https://api.blackhole2.ai.innopolis.university:443")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "pershin-medailab")
MINIO_PREFIX = os.environ.get("MINIO_PREFIX", "Pathomorphology/CAMELYON")


def get_minio_client():
    """Создает S3-клиент для MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
        verify=True,
    )


def list_slides_from_minio(prefix: str = f"{MINIO_PREFIX}/16/", extensions: List[str] = None) -> List[str]:
    """Получает список всех слайдов из MinIO."""
    if extensions is None:
        extensions = [".tif", ".tiff", ".ndpi", ".svs"]
    
    client = get_minio_client()
    slides = []
    
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=MINIO_BUCKET, Prefix=prefix):
        if 'Contents' not in page:
            continue
        for obj in page['Contents']:
            key = obj['Key']
            if any(key.endswith(ext) for ext in extensions):
                slides.append(key)
    
    print(f"Найдено слайдов в MinIO: {len(slides)}")
    return slides


def download_slide_from_minio(slide_key: str, local_dir: str) -> str:
    """Скачивает слайд из MinIO в локальную директорию."""
    client = get_minio_client()
    local_path = Path(local_dir) / Path(slide_key).name
    
    if local_path.exists():
        print(f"  [SKIP] Уже скачан: {local_path.name}")
        return str(local_path)
    
    print(f"  Скачиваю {Path(slide_key).name}...")
    client.download_file(MINIO_BUCKET, slide_key, str(local_path))
    print(f"  [OK] Скачан: {local_path.name}")
    return str(local_path)


# ============================================================
# Аннотации (XML)
# ============================================================

def parse_annotation_xml(xml_path: str) -> List[Dict]:
    """
    Парсит XML аннотацию CAMELYON16.
    Возвращает список аннотаций с координатами.
    """
    import re

    # Читаем XML и удаляем namespace (CAMELYON16 использует ASAPAnnotations namespace)
    with open(xml_path, 'r', encoding='utf-8') as f:
        xml_text = f.read()
    xml_clean = re.sub(r'xmlns="[^"]*"', '', xml_text)
    root = ET.fromstring(xml_clean)

    annotations = []

    for annotation in root.findall('.//Annotation'):
        ann_id = annotation.get('Id')
        ann_type = annotation.get('Type')

        coordinates = []
        for coord_group in annotation.findall('.//Coordinates'):
            for coord in coord_group.findall('Coordinate'):
                x = float(coord.get('X'))
                y = float(coord.get('Y'))
                coordinates.append((x, y))

        annotations.append({
            'id': ann_id,
            'type': ann_type,
            'coordinates': coordinates,
        })

    return annotations


def download_annotations_for_slide(slide_key: str, local_dir: str) -> Optional[str]:
    """Скачивает XML аннотации для слайда из MinIO."""
    client = get_minio_client()
    slide_name = Path(slide_key).stem
    
    # Возможные пути к аннотациям
    annotation_keys = [
        f"{MINIO_PREFIX}/16/training/annotations/{slide_name}.xml",
        f"{MINIO_PREFIX}/16/testing/annotations/{slide_name}.xml",
        f"{MINIO_PREFIX}/16/training/tumor/annotations/{slide_name}.xml",
    ]
    
    for ann_key in annotation_keys:
        try:
            client.head_object(Bucket=MINIO_BUCKET, Key=ann_key)
            local_ann_path = Path(local_dir) / f"{slide_name}_annotation.xml"
            client.download_file(MINIO_BUCKET, ann_key, str(local_ann_path))
            print(f"  [OK] Аннотация скачана: {local_ann_path.name}")
            return str(local_ann_path)
        except client.exceptions.ClientError:
            continue
    
    print(f"  [WARN] Аннотация не найд для {slide_name}")
    return None


# ============================================================
# Визуализация аннотаций на тайлах
# ============================================================

def is_tile_in_annotation(tile_x: int, tile_y: int, tile_size: int, annotations: List[Dict], slide_dimensions: Tuple[int, int]) -> bool:
    """
    Проверяет, попадает ли тайл в область аннотации (лимфома).

    Аргументы:
    ----------
    tile_x, tile_y: координаты верхнего левого угла тайла (level 0)
    tile_size: размер тайла
    annotations: список аннотаций из XML
    slide_dimensions: (width, height) слайда
    """
    tile_center = (tile_x + tile_size / 2, tile_y + tile_size / 2)

    for ann in annotations:
        if len(ann['coordinates']) < 3:
            continue

        polygon = ann['coordinates']

        # Проверяем центр тайла
        from matplotlib.path import Path
        polygon_path = Path(polygon)
        if polygon_path.contains_point(tile_center):
            return True

        # Проверяем углы тайла
        tile_corners = [
            (tile_x, tile_y),
            (tile_x + tile_size, tile_y),
            (tile_x + tile_size, tile_y + tile_size),
            (tile_x, tile_y + tile_size),
        ]
        if polygon_path.contains_points(tile_corners).any():
            return True

    return False


def mark_tiles_with_annotations(
    tiles_dir: str,
    annotations: List[Dict],
    slide_dimensions: Tuple[int, int],
    tile_size: int = 256,
    output_dir: str = None,
) -> List[Dict]:
    """
    Помечает тайлы, которые содержат аннотации (лимфомы).
    Создает копии тайлов с красной рамкой для помеченных.
    
    Возвращает список метаданных тайлов с метками.
    """
    if output_dir is None:
        output_dir = str(Path(tiles_dir).parent / "marked_tiles")
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    tiles_info = []
    tile_images = sorted(Path(tiles_dir).glob("*.png"))
    
    print(f"\nПомечаю тайлы с аннотациями...")
    
    for tile_img_path in tqdm(tile_images, desc="Marking tiles"):
        # Извлекаем координаты из имени файла
        # Поддерживаемые форматы:
        #   00256x_00512y.png  (формат gigapath preprocessing)
        #   000256_000512.png  (альтернативный формат)
        name = tile_img_path.stem

        try:
            if 'x_' in name:
                # Формат: 00256x_00512y
                parts = name.split('_')
                tile_x = int(parts[0].replace('x', ''))
                tile_y = int(parts[1].replace('y', ''))
            else:
                # Формат: 000256_000512
                parts = name.split('_')
                tile_x = int(parts[0])
                tile_y = int(parts[1])
        except Exception:
            print(f"  [WARN] Не удалось распарсить координаты: {tile_img_path.name}")
            continue
        
        # Проверяем, попадает ли в аннотацию
        has_lymphoma = is_tile_in_annotation(tile_x, tile_y, tile_size, annotations, slide_dimensions)
        
        tiles_info.append({
            'tile_path': str(tile_img_path),
            'tile_x': tile_x,
            'tile_y': tile_y,
            'has_lymphoma': has_lymphoma,
        })
        
        # Если есть лимфома — создаем копию с красной рамкой
        if has_lymphoma:
            img = Image.open(tile_img_path).convert('RGB')
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            # Красная рамка 5px
            for i in range(5):
                draw.rectangle(
                    [i, i, img.width - i - 1, img.height - i - 1],
                    outline='red',
                    width=2,
                )
            output_tile_path = output_path / tile_img_path.name
            img.save(output_tile_path)
    
    print(f"\nВсего тайлов: {len(tiles_info)}")
    print(f"С лимфомой: {sum(1 for t in tiles_info if t['has_lymphoma'])}")
    print(f"Без лимфомы: {sum(1 for t in tiles_info if not t['has_lymphoma'])}")
    
    # Сохраняем метаданные
    import json
    metadata_path = Path(output_dir) / "tiles_metadata.json"
    with open(metadata_path, 'w') as f:
        json.dump(tiles_info, f, indent=2)
    print(f"Метаданные сохранены: {metadata_path}")
    
    return tiles_info


# ============================================================
# Полный пайплайн для одного слайда
# ============================================================

def process_single_slide(
    slide_key: str,
    output_base_dir: str,
    level: int = 1,
    tile_size: int = 256,
    batch_size: int = 64,
    use_gpu: bool = False,
    skip_tiling: bool = False,
    skip_annotation_marking: bool = False,
) -> Dict:
    """
    Полный пайплайн для одного слайда:
    1. Скачивание из MinIO
    2. Тайлинг
    3. Tile encoder
    4. Slide encoder
    5. Разметка лимфом
    
    Возвращает словарь с путями к результатам.
    """
    slide_name = Path(slide_key).stem
    slide_output_dir = Path(output_base_dir) / slide_name
    slide_output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'=' * 70}")
    print(f"СЛАЙД: {slide_name}")
    print(f"{'=' * 70}")
    
    results = {
        'slide_name': slide_name,
        'slide_key': slide_key,
    }
    
    # ============================================================
    # Шаг 1: Скачивание слайда
    # ============================================================
    print("\n[1/5] Скачивание слайда из MinIO...")
    slide_path = download_slide_from_minio(slide_key, str(slide_output_dir))
    results['slide_path'] = slide_path
    
    # ============================================================
    # Шаг 2: Тайлинг
    # ============================================================
    tiles_dir = slide_output_dir / "tiles"
    
    if not skip_tiling and not tiles_dir.exists():
        print("\n[2/5] Тайлинг слайда...")
        print(f"NOTE: Prov-GigaPath обучен на слайдах с 0.5 mpp")

        try:
            tile_one_slide(
                slide_file=slide_path,
                save_dir=str(slide_output_dir / "tiling_output"),
                level=level,
                tile_size=tile_size,
            )

            # Копируем тайлы в удобную директорию
            slide_id_from_path = Path(slide_path).name
            tiling_tiles = slide_output_dir / "tiling_output" / "output" / slide_id_from_path
            if tiling_tiles.exists():
                shutil.copytree(str(tiling_tiles), str(tiles_dir), dirs_exist_ok=True)
            else:
                # Попробуем найти тайлы в поддиректории
                for d in (slide_output_dir / "tiling_output" / "output").iterdir():
                    if d.is_dir():
                        shutil.copytree(str(d), str(tiles_dir), dirs_exist_ok=True)
                        break
        except Exception as e:
            print(f"[ERROR] Тайлинг не удался: {e}")
            print(f"  Пропускаю слайд {slide_name}")
            return results
    else:
        print(f"\n[2/5] Пропускаю тайлинг, использую {tiles_dir}")
    
    results['tiles_dir'] = str(tiles_dir)
    
    # Собираем пути к тайлам
    tile_paths = sorted([str(p) for p in Path(tiles_dir).glob("*.png")])
    print(f"\nНайдено тайлов: {len(tile_paths)}")
    
    if len(tile_paths) == 0:
        print("[ERROR] Тайлы не найдены!")
        return results
    
    # ============================================================
    # Шаг 3: Tile Encoder
    # ============================================================
    print("\n[3/5] Tile encoder (извлечение фич)...")
    
    device = torch.device('cuda' if use_gpu and torch.cuda.is_available() else 'cpu')
    print(f"Device: {device}")
    
    tile_encoder = timm.create_model("hf_hub:prov-gigapath/prov-gigapath", pretrained=True)
    tile_encoder.to(device)
    tile_encoder.eval()
    
    transform = load_tile_encoder_transforms()
    
    # Dataset и DataLoader
    class TileDataset(Dataset):
        def __init__(self, paths, transform):
            self.paths = paths
            self.transform = transform
        
        def __len__(self):
            return len(self.paths)
        
        def __getitem__(self, idx):
            img_path = self.paths[idx]
            img_name = Path(img_path).stem

            # Парсим координаты из имени файла
            # Форматы: 00256x_00512y или 000256_000512
            try:
                if 'x_' in img_name:
                    parts = img_name.split('_')
                    x = int(parts[0].replace('x', ''))
                    y = int(parts[1].replace('y', ''))
                else:
                    parts = img_name.split('_')
                    x = int(parts[0])
                    y = int(parts[1])
            except Exception:
                x, y = 0, 0
            
            img = Image.open(img_path).convert('RGB')
            if self.transform:
                img = self.transform(img)
            
            return {
                'img': img,
                'coords': torch.tensor([x, y], dtype=torch.float32),
            }
    
    dataset = TileDataset(tile_paths, transform)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0)
    
    tile_embeds_list = []
    coords_list = []
    
    for batch in tqdm(dataloader, desc="Tile inference"):
        imgs = batch['img'].to(device)
        with torch.no_grad():
            if use_gpu and torch.cuda.is_available():
                with torch.cuda.amp.autocast(dtype=torch.float16):
                    embeds = tile_encoder(imgs).cpu()
            else:
                embeds = tile_encoder(imgs).cpu()
        
        tile_embeds_list.append(embeds)
        coords_list.append(batch['coords'])
    
    tile_embeds = torch.cat(tile_embeds_list, dim=0)
    coords = torch.cat(coords_list, dim=0)
    
    print(f"Tile embeddings shape: {tile_embeds.shape}")
    print(f"Coords shape: {coords.shape}")
    
    # Сохраняем
    tile_embeds_path = slide_output_dir / f"{slide_name}_tile_embeddings.pt"
    torch.save({
        'tile_embeds': tile_embeds,
        'coords': coords,
        'tile_paths': tile_paths,
    }, tile_embeds_path)
    results['tile_embeddings_path'] = str(tile_embeds_path)
    
    # ============================================================
    # Шаг 4: Slide Encoder
    # ============================================================
    print("\n[4/5] Slide encoder...")
    
    try:
        from gigapath import slide_encoder as se_module
        
        slide_encoder_model = se_module.create_model(
            "hf_hub:prov-gigapath/prov-gigapath",
            "gigapath_slide_enc12l768d",
            in_chans=1536,
        )
        slide_encoder_model.to(device)
        slide_encoder_model.eval()
        
        # Добавляем batch dimension
        if len(tile_embeds.shape) == 2:
            tile_embeds_batch = tile_embeds.unsqueeze(0).to(device)
            coords_batch = coords.unsqueeze(0).to(device)
        else:
            tile_embeds_batch = tile_embeds.to(device)
            coords_batch = coords.to(device)
        
        with torch.no_grad():
            slide_embeds = slide_encoder_model(
                tile_embeds_batch, 
                coords_batch, 
                all_layer_embed=True
            )
        
        # Сохраняем
        slide_output_path = slide_output_dir / f"{slide_name}_slide_embeddings.h5"
        with h5py.File(slide_output_path, "w") as f:
            for i, embed in enumerate(slide_embeds):
                f.create_dataset(f"layer_{i}_embed", data=embed.cpu().numpy())
            f.create_dataset("last_layer_embed", data=slide_embeds[-1].cpu().numpy())
            f.create_dataset("coords", data=coords.numpy())
        
        print(f"Slide embeddings saved: {slide_output_path}")
        results['slide_embeddings_path'] = str(slide_output_path)
        
    except Exception as e:
        print(f"[ERROR] Ошибка slide encoder: {e}")
        import traceback
        traceback.print_exc()
    
    # ============================================================
    # Шаг 5: Разметка лимфом по аннотациям
    # ============================================================
    if not skip_annotation_marking:
        print("\n[5/5] Разметка лимфом по аннотациям...")
        
        ann_path = download_annotations_for_slide(slide_key, str(slide_output_dir))
        
        if ann_path:
            annotations = parse_annotation_xml(ann_path)
            print(f"  Найдено аннотаций: {len(annotations)}")
            
            # Получаем размеры слайда
            slide = OpenSlide(slide_path)
            slide_dims = slide.dimensions
            slide.close()
            
            # Помечаем тайлы
            tiles_info = mark_tiles_with_annotations(
                tiles_dir=str(tiles_dir),
                annotations=annotations,
                slide_dimensions=slide_dims,
                tile_size=tile_size,
                output_dir=str(slide_output_dir / "marked_tiles"),
            )
            
            results['annotations_path'] = ann_path
            results['tiles_with_lymphoma'] = sum(1 for t in tiles_info if t['has_lymphoma'])
            results['total_tiles'] = len(tiles_info)
        else:
            print("  [WARN] Нет аннотаций для этого слайда")
    else:
        print("\n[5/5] Пропускаю разметку аннотаций")
    
    print(f"\n{'=' * 70}")
    print(f"ГОТОВО: {slide_name}")
    print(f"{'=' * 70}")
    
    return results


# ============================================================
# ClearML: выполнение на агенте
# ============================================================

def _run_on_agent(task: Task, config: dict):
    """
    Выполняется на ClearML агенте (или локально если не remote).
    """
    logger = task.get_logger()

    slide_keys = config.get('slide_keys', None) or []

    # Если слайды не указаны — сканируем MinIO
    if not slide_keys:
        print("Сканирую MinIO для получения списка слайдов...")
        slide_keys = list_slides_from_minio()

    # Ограничиваем количество
    max_slides = config.get('max_slides', None)
    if max_slides:
        slide_keys = slide_keys[:max_slides]

    print(f"Будет обработано слайдов: {len(slide_keys)}")

    # Директория для результатов
    output_base = "outputs/clearml_pipeline"
    os.makedirs(output_base, exist_ok=True)

    # Запускаем пайплайн
    all_results = []

    for i, slide_key in enumerate(slide_keys):
        print(f"\n{'=' * 70}")
        print(f"СЛАЙД {i + 1}/{len(slide_keys)}")
        print(f"{'=' * 70}")

        try:
            result = process_single_slide(
                slide_key=slide_key,
                output_base_dir=output_base,
                level=config['level'],
                tile_size=config['tile_size'],
                batch_size=config['batch_size'],
                use_gpu=config['use_gpu'],
                skip_tiling=config['skip_tiling'],
                skip_annotation_marking=config['skip_annotation_marking'],
            )
            all_results.append(result)
            logger.report_text(f"Обработан слайд {slide_key}")

        except Exception as e:
            print(f"[ERROR] Ошибка обработки слайда {slide_key}: {e}")
            import traceback
            traceback.print_exc()
            logger.report_text(f"ОШИБКА: {slide_key} — {e}")

    # Итоговая статистика
    print(f"\n{'=' * 70}")
    print(f"ИТОГО:")
    print(f"  Обработано слайдов: {len(all_results)}/{len(slide_keys)}")
    print(f"  Всего тайлов: {sum(r.get('total_tiles', 0) for r in all_results)}")
    print(f"  Тайлов с лимфомой: {sum(r.get('tiles_with_lymphoma', 0) for r in all_results)}")
    print(f"{'=' * 70}")

    logger.report_single_value("slides_processed", len(all_results))
    logger.report_single_value(
        "total_tiles",
        sum(r.get('total_tiles', 0) for r in all_results)
    )
    logger.report_single_value(
        "tiles_with_lymphoma",
        sum(r.get('tiles_with_lymphoma', 0) for r in all_results)
    )

    # Сохраняем артефакты
    import json
    summary_path = os.path.join(output_base, "pipeline_summary.json")
    with open(summary_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)

    task.upload_artifact("pipeline_summary", summary_path)
    print(f"\nРезультаты сохранены: {output_base}")


# ============================================================
# Main
# ============================================================

def main():
    # Проверка HF_TOKEN — нужен для загрузки модели с HuggingFace
    if "HF_TOKEN" not in os.environ:
        print("=" * 60)
        print("ВНИМАНИЕ: HF_TOKEN не установлен!")
        print("Для загрузки модели Prov-GigaPath необходим HuggingFace токен.")
        print("Получите токен: https://huggingface.co/settings/tokens")
        print("Установите: export HF_TOKEN=your_token")
        print("=" * 60)
        # Не падаем — может быть локальный тест без модели
        print("Продолжаю без HF_TOKEN (загрузка модели может завершиться ошибкой)...")

    parser = argparse.ArgumentParser(
        description="Prov-GigaPath полный пайплайн в ClearML"
    )
    parser.add_argument("--local", action="store_true", help="Локальный запуск (без ClearML)")
    parser.add_argument("--slide", type=str, nargs="+", default=None, help="Конкретные слайды для обработки")
    parser.add_argument("--level", type=int, default=1, help="Magnification level (0=highest)")
    parser.add_argument("--tile_size", type=int, default=256, help="Размер тайла")
    parser.add_argument("--batch_size", type=int, default=64, help="Batch size")
    parser.add_argument("--gpu", action="store_true", help="Использовать GPU")
    parser.add_argument("--max_slides", type=int, default=None, help="Лимит слайдов (для теста)")
    parser.add_argument("--skip_tiling", action="store_true", help="Пропустить тайлинг")
    parser.add_argument("--skip_annotations", action="store_true", help="Пропустить разметку лимфом")

    args = parser.parse_args()

    if args.local:
        # ============================================================
        # Локальный запуск (без ClearML)
        # ============================================================
        print("ЛОКАЛЬНЫЙ ЗАПУСК")

        slide_keys = args.slide
        if slide_keys is None:
            print("Сканирую MinIO...")
            slide_keys = list_slides_from_minio()

        if args.max_slides:
            slide_keys = slide_keys[:args.max_slides]

        output_base = "outputs/local_pipeline"
        os.makedirs(output_base, exist_ok=True)

        for i, slide_key in enumerate(slide_keys):
            print(f"\nСлайд {i + 1}/{len(slide_keys)}")
            process_single_slide(
                slide_key=slide_key,
                output_base_dir=output_base,
                level=args.level,
                tile_size=args.tile_size,
                batch_size=args.batch_size,
                use_gpu=args.gpu,
                skip_tiling=args.skip_tiling,
                skip_annotation_marking=args.skip_annotations,
            )
    else:
        # ============================================================
        # Запуск через ClearML
        # Работает в двух режимах:
        #   1. Локально  — отправляет задачу на агент (execute_remotely)
        #   2. На агенте — выполняет пайплайн (Task.init только логирует)
        # ============================================================
        task = Task.init(
            project_name="pershin-medailab",
            task_name="Pathomorphology",
            task_type=Task.TaskTypes.inference,
        )

        # Конфигурация (отображается в ClearML UI)
        config = {
            'slide_keys': args.slide,
            'level': args.level,
            'tile_size': args.tile_size,
            'batch_size': args.batch_size,
            'use_gpu': args.gpu,
            'max_slides': args.max_slides,
            'skip_tiling': args.skip_tiling,
            'skip_annotation_marking': args.skip_annotations,
            'minio_bucket': MINIO_BUCKET,
            'minio_prefix': MINIO_PREFIX,
        }
        task.connect(config)

        # Проверяем — мы уже на агенте или ещё локально?
        # CLEARML_TASK_ID устанавливается агентом при удалённом запуске
        if "CLEARML_TASK_ID" not in os.environ:
            # Ещё не на агенте — отправляем задачу
            print("Отправляю задачу на агент (очередь: default)...")
            task.execute_remotely(queue_name="default", exit_process=True)

        # Код ниже выполняется ТОЛЬКО на агенте ClearML
        print("\nВыполняюсь на ClearML агенте")
        _run_on_agent(task, config)
        task.close()


if __name__ == "__main__":
    main()
