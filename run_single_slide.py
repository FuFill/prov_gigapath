"""
Полный pipeline для инференса одного WSI-слайда через GigaPath:
1. Разбиение WSI на тайлы (popixelная обработка, без загрузки всего слайда в память)
2. Получение эмбеддингов тайлов через tile encoder
3. Получение эмбеддингов слайда через slide encoder

Пример:
    python run_single_slide.py --slide_path data/000002.ndpi --output_dir outputs/slide_inference
"""

import os
import sys
import argparse
import torch
import timm
import h5py
import numpy as np
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Переопределяем TEMP на D:\
if os.path.exists("D:\\"):
    os.environ["TMPDIR"] = "D:\\temp_gigapath"
    os.environ["TEMP"] = "D:\\temp_gigapath"
    os.environ["TMP"] = "D:\\temp_gigapath"
    os.makedirs("D:\\temp_gigapath", exist_ok=True)

from openslide import OpenSlide
from PIL import Image
from torchvision import transforms
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm
from typing import List, Tuple


# ============================================================
# Тайлинг без загрузки всего слайда в память
# ============================================================

def get_slide_dimensions(slide: OpenSlide) -> Tuple[int, int]:
    """Возвращает размеры слайда на level 0."""
    return slide.dimensions  # (width, height)


def get_level_dimensions(slide: OpenSlide, level: int) -> Tuple[int, int]:
    """Возвращает размеры слайда на указанном level."""
    return slide.level_dimensions[level]


def get_level_downsample(slide: OpenSlide, level: int) -> float:
    """Возвращает даунсемпл фактор для уровня."""
    return slide.level_downsamples[level]


def generate_tile_grid(slide: OpenSlide, level: int, tile_size: int) -> List[Tuple[int, int]]:
    """
    Генерирует сетку тайлов для слайда.
    Возвращает список координат (x, y) в системе level 0.
    """
    level_dim = get_level_dimensions(slide, level)
    downsample = get_level_downsample(slide, level)
    
    # tile_size указан для level 0, масштабируем для текущего level
    level_tile_size = int(tile_size / downsample)
    
    coords = []
    for y in range(0, level_dim[1], level_tile_size):
        for x in range(0, level_dim[0], level_tile_size):
            # Конвертируем координаты обратно в level 0
            x0 = int(x * downsample)
            y0 = int(y * downsample)
            coords.append((x0, y0))
    
    return coords


def extract_tiles_from_slide(
    slide_path: str,
    level: int,
    tile_size: int,
    output_dir: str,
    batch_size: int = 64,
) -> Tuple[Path, int]:
    """
    Извлекает тайлы из WSI по одному, без загрузки всего слайда.
    Возвращает путь к директории с тайлами и количество тайлов.
    """
    tiles_dir = Path(output_dir) / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Открываю слайд: {slide_path}")
    slide = OpenSlide(slide_path)
    
    level_dim = get_level_dimensions(slide, level)
    downsample = get_level_downsample(slide, level)
    level_tile_size = int(tile_size / downsample)
    
    print(f"Размер слайда на level {level}: {level_dim}")
    print(f"Даунсемпл: {downsample:.2f}")
    print(f"Размер тайла на level {level}: {level_tile_size}x{level_tile_size}")
    
    tile_count = 0
    
    # Итерируемся по сетке
    for y in tqdm(range(0, level_dim[1], level_tile_size), desc="Rows"):
        for x in range(0, level_dim[0], level_tile_size):
            try:
                # Читаем регион на нужном level
                region = slide.read_region((x, y), level, (level_tile_size, level_tile_size))
                
                # Конвертируем в RGB
                region = region.convert("RGB")
                
                # Ресайзим до tile_size (256x256)
                if region.size != (tile_size, tile_size):
                    region = region.resize((tile_size, tile_size), Image.LANCZOS)
                
                # Сохраняем тайл
                tile_name = f"{x:06d}_{y:06d}.png"
                region.save(tiles_dir / tile_name)
                tile_count += 1
                
            except Exception as e:
                print(f"\nОшибка при чтении тайла ({x}, {y}): {e}")
                continue
    
    slide.close()
    print(f"\nИзвлечено тайлов: {tile_count}")
    return tiles_dir, tile_count


# ============================================================
# Tile Encoder Dataset
# ============================================================

class TileDataset(Dataset):
    def __init__(self, image_paths: List[str], transform=None):
        self.transform = transform
        self.image_paths = image_paths

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        img_name = os.path.basename(img_path)
        # Извлекаем координаты из имени файла: xxxxxx_yyyyyy.png
        parts = img_name.replace(".png", "").split("_")
        x, y = int(parts[0]), int(parts[1])
        
        img = Image.open(img_path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        
        return {
            "img": img,
            "coords": torch.tensor([x, y], dtype=torch.float32)
        }


def get_tile_transform():
    """Трансформации для tile encoder (как в оригинальном GigaPath)."""
    return transforms.Compose([
        transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
    ])


# ============================================================
# Main Pipeline
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Run full GigaPath inference on a single WSI slide")
    parser.add_argument("--slide_path", type=str, required=True, help="Path to the WSI file (.ndpi, .svs, etc.)")
    parser.add_argument("--output_dir", type=str, default="outputs/slide_inference", help="Output directory")
    parser.add_argument("--tile_size", type=int, default=256, help="Tile size in pixels")
    parser.add_argument("--level", type=int, default=1, help="Magnification level (0=highest, 1=lower, use 1+ for large slides)")
    parser.add_argument("--batch_size", type=int, default=64, help="Batch size for tile encoder")
    parser.add_argument("--skip_tiling", action="store_true", default=False, help="Skip tiling if tiles already exist")
    parser.add_argument("--skip_slide_encoder", action="store_true", default=False, help="Only run tile encoder")
    parser.add_argument("--global_pool", action="store_true", default=False, help="Use global pooling in slide encoder")
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    slide_path = args.slide_path
    slide_id = Path(slide_path).stem
    print(f"Slide: {slide_path}")
    print(f"Slide ID: {slide_id}")
    print(f"Device: CPU (no GPU)")
    print()
    
    # ============================================================
    # Шаг 1: Тайлинг
    # ============================================================
    tiles_dir = output_dir / "tiles"
    
    if not args.skip_tiling:
        print("=" * 60)
        print("ШАГ 1: Разбиение WSI на тайлы (memory-safe)")
        print("=" * 60)
        
        tiles_dir, tile_count = extract_tiles_from_slide(
            slide_path=slide_path,
            level=args.level,
            tile_size=args.tile_size,
            output_dir=str(output_dir),
        )
        
        if tile_count == 0:
            print("ОШИБКА: Не удалось извлечь тайлы.")
            sys.exit(1)
    else:
        print(f"Пропускаю тайлинг, использую {tiles_dir}")
    
    # Собираем пути к тайлам
    tile_paths = sorted([str(p) for p in Path(tiles_dir).glob("*.png")])
    print(f"\nНайдено тайлов: {len(tile_paths)}")
    
    if len(tile_paths) == 0:
        print("ОШИБКА: Тайлы не найдены.")
        sys.exit(1)
    
    # ============================================================
    # Шаг 2: Загрузка tile encoder
    # ============================================================
    print("\n" + "=" * 60)
    print("ШАГ 2: Загрузка tile encoder (download from HF if needed)")
    print("=" * 60)
    
    print("Загружаю tile encoder из HuggingFace Hub...")
    tile_encoder = timm.create_model("hf_hub:prov-gigapath/prov-gigapath", pretrained=True)
    tile_encoder.eval()
    print(f"Tile encoder загружен. Параметры: {sum(p.numel() for p in tile_encoder.parameters()):,}")
    
    # ============================================================
    # Шаг 3: Инференс tile encoder
    # ============================================================
    print("\n" + "=" * 60)
    print(f"ШАГ 3: Инференс tile encoder ({len(tile_paths)} тайлов, batch_size={args.batch_size})")
    print("=" * 60)
    
    transform = get_tile_transform()
    dataset = TileDataset(tile_paths, transform=transform)
    dataloader = DataLoader(dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)
    
    tile_embeds_list = []
    coords_list = []
    
    for batch in tqdm(dataloader, desc="Tile encoder"):
        with torch.no_grad():
            embeds = tile_encoder(batch["img"]).cpu()
        tile_embeds_list.append(embeds)
        coords_list.append(batch["coords"])
    
    tile_embeds = torch.cat(tile_embeds_list, dim=0)
    coords = torch.cat(coords_list, dim=0)
    
    print(f"Tile embeddings shape: {tile_embeds.shape}")
    print(f"Coords shape: {coords.shape}")
    
    # Сохраняем эмбеддинги тайлов
    tile_embeds_path = output_dir / f"{slide_id}_tile_embeddings.pt"
    torch.save({"tile_embeds": tile_embeds, "coords": coords}, tile_embeds_path)
    print(f"Сохранено в: {tile_embeds_path}")
    
    # ============================================================
    # Шаг 4: Slide encoder (опционально)
    # ============================================================
    if args.skip_slide_encoder:
        print("\nПропускаю slide encoder (--skip_slide_encoder)")
        print(f"Tile embeddings: {tile_embeds_path}")
        return
    
    print("\n" + "=" * 60)
    print("ШАГ 4: Загрузка slide encoder")
    print("=" * 60)
    
    from gigapath import slide_encoder as se_module
    
    slide_encoder_model = se_module.create_model(
        "hf_hub:prov-gigapath/prov-gigapath",
        "gigapath_slide_enc12l768d",
        in_chans=1536,
        global_pool=args.global_pool,
    )
    slide_encoder_model.eval()
    print(f"Slide encoder загружен. Параметры: {sum(p.numel() for p in slide_encoder_model.parameters()):,}")
    
    # ============================================================
    # Шаг 5: Инференс slide encoder
    # ============================================================
    print("\n" + "=" * 60)
    print("ШАГ 5: Инференс slide encoder")
    print("=" * 60)
    
    if len(tile_embeds.shape) == 2:
        tile_embeds = tile_embeds.unsqueeze(0)
        coords = coords.unsqueeze(0)
    
    with torch.no_grad():
        slide_embeds = slide_encoder_model(tile_embeds, coords, all_layer_embed=True)
    
    results = {f"layer_{i}_embed": slide_embeds[i].cpu() for i in range(len(slide_embeds))}
    results["last_layer_embed"] = slide_embeds[-1].cpu()
    
    # Печатаем размеры
    for key, value in results.items():
        print(f"  {key}: {value.shape}")
    
    # Сохраняем
    slide_output_path = output_dir / f"{slide_id}_slide_embeddings.h5"
    with h5py.File(slide_output_path, "w") as f:
        for key, value in results.items():
            f.create_dataset(key, data=value.numpy())
        f.create_dataset("coords", data=coords.numpy())
    
    print(f"\nSlide embeddings saved: {slide_output_path}")
    
    # ============================================================
    # Итог
    # ============================================================
    print("\n" + "=" * 60)
    print("ГОТОВО!")
    print("=" * 60)
    print(f"Tile embeddings: {tile_embeds_path}")
    print(f"Slide embeddings: {slide_output_path}")
    print(f"Number of tiles: {len(tile_paths)}")
    print(f"Final slide embedding dim: {results['last_layer_embed'].shape}")


if __name__ == "__main__":
    main()
