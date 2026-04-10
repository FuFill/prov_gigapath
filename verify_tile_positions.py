"""
Скрипт для проверки корректности позиций тайлов Prov-GigaPath.
Сравнивает координаты тайлов с XML аннотациями и визуализирует результаты.

Использование:
    python verify_tile_positions.py --slide <slide_path> --annotation <xml_path> --tiles <tiles_dir>
"""

import os
import sys
import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Tuple
from PIL import Image, ImageDraw
import numpy as np
from matplotlib.path import Path as MPLPath
import matplotlib.pyplot as plt


def parse_annotation_xml(xml_path: str) -> List[Dict]:
    """Парсит XML аннотацию CAMELYON16."""
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


def parse_tile_coordinates(tile_path: str) -> Tuple[int, int]:
    """
    Парсит координаты из имени файла тайла.
    Поддерживаемые форматы:
    - 00256x_00512y.png  (формат gigapath preprocessing)
    - 000256_000512.png  (альтернативный формат)
    """
    name = Path(tile_path).stem

    if 'x_' in name:
        # Формат: 00256x_00512y
        parts = name.split('_')
        x = int(parts[0].replace('x', ''))
        y = int(parts[1].replace('y', ''))
        return x, y

    # Формат: 000256_000512
    parts = name.split('_')
    if len(parts) >= 2:
        x = int(parts[0])
        y = int(parts[1])
        return x, y

    raise ValueError(f"Не удалось распарсить координаты из: {name}")


def is_point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    """Проверяет, находится ли точка внутри полигона."""
    path = MPLPath(polygon)
    return path.contains_point(point)


def is_tile_in_annotation(
    tile_x: int, 
    tile_y: int, 
    tile_size: int, 
    annotations: List[Dict]
) -> Tuple[bool, List[str]]:
    """
    Проверяет, попадает ли тайл в область аннотации.
    
    Возвращает:
    - bool: True если тайл содержит аннотацию
    - List[str]: ID аннотаций, в которые попадает тайл
    """
    tile_center = (tile_x + tile_size / 2, tile_y + tile_size / 2)
    tile_corners = [
        (tile_x, tile_y),
        (tile_x + tile_size, tile_y),
        (tile_x + tile_size, tile_y + tile_size),
        (tile_x, tile_y + tile_size),
    ]
    
    matching_annotations = []
    
    for ann in annotations:
        if len(ann['coordinates']) < 3:
            continue
        
        polygon = ann['coordinates']
        
        # Проверяем центр тайла
        if is_point_in_polygon(tile_center, polygon):
            matching_annotations.append(ann['id'])
            continue
        
        # Проверяем углы тайла
        for corner in tile_corners:
            if is_point_in_polygon(corner, polygon):
                matching_annotations.append(ann['id'])
                break
        
        # Проверяем пересечение границ
        if not matching_annotations:
            path = MPLPath(polygon)
            if path.contains_points(tile_corners).any():
                matching_annotations.append(ann['id'])
    
    return len(matching_annotations) > 0, matching_annotations


def visualize_tile_on_slide(
    slide_path: str,
    tile_x: int,
    tile_y: int,
    tile_size: int,
    annotations: List[Dict],
    output_path: str,
    level: int = 1,
):
    """Визуализирует тайл на слайде с аннотациями."""
    from openslide import OpenSlide
    
    slide = OpenSlide(slide_path)
    
    # Получаем даунсемпл
    downsample = slide.level_downsamples[level]
    
    # Читаем регион вокруг тайла
    region_size = int(tile_size * 3)  # 3x больше тайла
    x_start = max(0, tile_x - region_size // 2)
    y_start = max(0, tile_y - region_size // 2)
    
    region = slide.read_region(
        (x_start, y_start),
        level,
        (region_size, region_size)
    )
    
    # Рисуем аннотации
    draw = ImageDraw.Draw(region)
    
    for ann in annotations:
        if len(ann['coordinates']) < 3:
            continue
        
        # Конвертируем координаты
        relative_coords = []
        for (x, y) in ann['coordinates']:
            rx = (x - x_start) / downsample
            ry = (y - y_start) / downsample
            relative_coords.append((rx, ry))
        
        # Рисуем полигон
        if len(relative_coords) >= 3:
            draw.polygon(relative_coords, outline='green', width=2)
    
    # Рисуем тайл (красная рамка)
    tile_x_rel = (tile_x - x_start) / downsample
    tile_y_rel = (tile_y - y_start) / downsample
    tile_size_rel = tile_size / downsample
    
    draw.rectangle(
        [tile_x_rel, tile_y_rel, tile_x_rel + tile_size_rel, tile_y_rel + tile_size_rel],
        outline='red',
        width=3,
    )
    
    region.save(output_path)
    slide.close()
    
    print(f"Визуализация сохранена: {output_path}")


def verify_all_tiles(
    tiles_dir: str,
    annotations: List[Dict],
    output_dir: str,
    tile_size: int = 256,
    max_tiles: int = None,
) -> Dict:
    """
    Проверяет все тайлы и создает отчет.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    tile_files = sorted(Path(tiles_dir).glob("*.png"))
    
    if max_tiles:
        tile_files = tile_files[:max_tiles]
    
    print(f"Проверка {len(tile_files)} тайлов...")
    
    results = {
        'total_tiles': len(tile_files),
        'tiles_in_annotation': 0,
        'tiles_not_in_annotation': 0,
        'tiles': [],
    }
    
    for tile_file in tile_files:
        try:
            tile_x, tile_y = parse_tile_coordinates(str(tile_file))
            
            in_ann, ann_ids = is_tile_in_annotation(
                tile_x, tile_y, tile_size, annotations
            )
            
            tile_result = {
                'file': tile_file.name,
                'x': tile_x,
                'y': tile_y,
                'in_annotation': in_ann,
                'annotation_ids': ann_ids,
            }
            
            results['tiles'].append(tile_result)
            
            if in_ann:
                results['tiles_in_annotation'] += 1
            else:
                results['tiles_not_in_annotation'] += 1
            
        except Exception as e:
            print(f"  [ERROR] {tile_file.name}: {e}")
    
    # Сохраняем отчет
    report_path = output_path / "tile_verification_report.json"
    with open(report_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'=' * 60}")
    print(f"ОТЧЕТ")
    print(f"{'=' * 60}")
    print(f"Всего тайлов: {results['total_tiles']}")
    print(f"В аннотации: {results['tiles_in_annotation']}")
    print(f"Не в аннотации: {results['tiles_not_in_annotation']}")
    print(f"{'=' * 60}")
    print(f"Отчет сохранен: {report_path}")
    
    return results


def create_summary_image(
    tiles_dir: str,
    annotations: List[Dict],
    output_path: str,
    tile_size: int = 256,
    max_display: int = 1000,
):
    """
    Создает сводное изображение с позициями тайлов и аннотациями.
    """
    tile_files = sorted(Path(tiles_dir).glob("*.png"))[:max_display]
    
    # Собираем все координаты
    tile_positions = []
    for tile_file in tile_files:
        try:
            x, y = parse_tile_coordinates(str(tile_file))
            tile_positions.append((x, y, str(tile_file)))
        except:
            pass
    
    if not tile_positions:
        print("Нет тайлов для отображения")
        return
    
    # Находим границы
    min_x = min(p[0] for p in tile_positions)
    max_x = max(p[0] for p in tile_positions)
    max_y = max(p[1] for p in tile_positions)
    
    # Создаем изображение
    scale = 0.1  # Масштаб для отображения
    img_width = int((max_x - min_x + tile_size) * scale) + 100
    img_height = int((max_y + tile_size) * scale) + 100
    
    img = Image.new('RGB', (img_width, img_height), 'white')
    draw = ImageDraw.Draw(img)
    
    # Рисуем аннотации
    for ann in annotations:
        if len(ann['coordinates']) < 3:
            continue
        
        coords = [(int((x - min_x) * scale + 50), int(y * scale + 50)) 
                  for x, y in ann['coordinates']]
        
        if len(coords) >= 3:
            draw.polygon(coords, outline='green', width=2)
    
    # Рисуем тайлы
    for x, y, path in tile_positions:
        x_img = int((x - min_x) * scale + 50)
        y_img = int(y * scale + 50)
        size_img = int(tile_size * scale)
        
        # Проверяем, в аннотации ли
        in_ann, _ = is_tile_in_annotation(x, y, tile_size, annotations)
        color = 'red' if in_ann else 'blue'
        
        draw.rectangle(
            [x_img, y_img, x_img + size_img, y_img + size_img],
            outline=color,
            width=1,
        )
    
    # Легенда
    draw.text((10, 10), "Green: Annotations", fill='green')
    draw.text((10, 30), "Red: Tiles in annotation", fill='red')
    draw.text((10, 50), "Blue: Tiles not in annotation", fill='blue')
    
    img.save(output_path)
    print(f"Сводное изображение сохранено: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Проверка корректности позиций тайлов Prov-GigaPath"
    )
    parser.add_argument("--slide", type=str, help="Путь к слайду (.tif, .ndpi)")
    parser.add_argument("--annotation", type=str, required=True, help="Путь к XML аннотации")
    parser.add_argument("--tiles", type=str, required=True, help="Директория с тайлами")
    parser.add_argument("--output", type=str, default="outputs/verification", help="Директория для результатов")
    parser.add_argument("--tile_size", type=int, default=256, help="Размер тайла")
    parser.add_argument("--max_tiles", type=int, default=None, help="Лимит тайлов для проверки")
    parser.add_argument("--visualize", type=int, nargs=2, metavar=('TILE_X', 'TILE_Y'),
                       help="Визуализировать конкретный тайл")
    parser.add_argument("--summary", action="store_true", help="Создать сводное изображение")
    
    args = parser.parse_args()
    
    # Парсим аннотации
    print(f"Загрузка аннотаций: {args.annotation}")
    annotations = parse_annotation_xml(args.annotation)
    print(f"Найдено аннотаций: {len(annotations)}")
    
    # Проверяем все тайлы
    results = verify_all_tiles(
        tiles_dir=args.tiles,
        annotations=annotations,
        output_dir=args.output,
        tile_size=args.tile_size,
        max_tiles=args.max_tiles,
    )
    
    # Визуализация конкретного тайла
    if args.visualize and args.slide:
        tile_x, tile_y = args.visualize
        output_vis = os.path.join(args.output, f"tile_{tile_x}_{tile_y}_visualization.png")
        
        visualize_tile_on_slide(
            slide_path=args.slide,
            tile_x=tile_x,
            tile_y=tile_y,
            tile_size=args.tile_size,
            annotations=annotations,
            output_path=output_vis,
        )
    
    # Сводное изображение
    if args.summary:
        summary_path = os.path.join(args.output, "tiles_summary.png")
        create_summary_image(
            tiles_dir=args.tiles,
            annotations=annotations,
            output_path=summary_path,
            tile_size=args.tile_size,
        )
    
    print("\nГОТОВО!")


if __name__ == "__main__":
    main()
