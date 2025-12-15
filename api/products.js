/**
 * Products API - Vercel Serverless Function
 *
 * Улучшенный API с пагинацией, метаданными и стандартным форматом ответа
 *
 * Query Parameters:
 *   - q: поиск по названию или SKU
 *   - category: фильтр по категории
 *   - brand: фильтр по бренду
 *   - min_credit: минимальная цена
 *   - max_credit: максимальная цена
 *   - in_stock: только товары в наличии (true/false)
 *   - limit: количество записей (default: 50, max: 200)
 *   - offset: смещение для пагинации (default: 0)
 *   - sort: поле сортировки (credit, name, stock)
 *   - order: направление сортировки (asc, desc)
 *
 * Response Format:
 * {
 *   "success": true,
 *   "data": [...products],
 *   "meta": {
 *     "total": 1500,
 *     "returned": 50,
 *     "limit": 50,
 *     "offset": 0,
 *     "has_more": true,
 *     "next_offset": 50
 *   },
 *   "filters_applied": {
 *     "category": "видеокарты",
 *     "min_credit": 100000,
 *     "max_credit": 300000
 *   }
 * }
 */

import products from '../data/products.json';

export default function handler(req, res) {
  // Добавляем CORS заголовки
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  try {
    const {
      q,
      category,
      brand,
      min_credit,
      max_credit,
      in_stock,
      limit = '50',
      offset = '0',
      sort = 'credit',
      order = 'asc'
    } = req.query;

    let result = [...products];
    const filtersApplied = {};

    // 1. Поиск по названию ИЛИ ТОЧНОЕ СОВПАДЕНИЕ ПО SKU
    if (q) {
      const qLower = q.toLowerCase().trim();
      filtersApplied.query = q;

      result = result.filter(p =>
        // 1. Точное совпадение SKU (наивысший приоритет)
        p.sku.toLowerCase() === qLower ||
        // 2. Поиск по названию
        p.name.toLowerCase().includes(qLower)
      );
    }

    // 2. Фильтр по category
    if (category) {
      const categoryLower = category.toLowerCase().trim();
      filtersApplied.category = category;

      result = result.filter(p =>
        p.category.toLowerCase() === categoryLower
      );
    }

    // 3. Фильтр по бренду
    if (brand) {
      const brandLower = brand.toLowerCase().trim();
      filtersApplied.brand = brand;

      result = result.filter(p =>
        p.brand && p.brand.toLowerCase() === brandLower
      );
    }

    // 4. Фильтрация по цене (Credit)
    if (min_credit || max_credit) {
      const min = parseFloat(min_credit || 0);
      const max = parseFloat(max_credit || Infinity);

      if (min_credit) filtersApplied.min_credit = min;
      if (max_credit) filtersApplied.max_credit = max;

      result = result.filter(p => {
        const creditPrice = parseFloat(p.credit || p.price || 0);
        return creditPrice >= min && creditPrice <= max;
      });
    }

    // 5. Фильтр по наличию
    if (in_stock === 'true') {
      filtersApplied.in_stock = true;
      result = result.filter(p => parseInt(p.stock || 0) > 0);
    }

    // 6. Сортировка
    const validSortFields = ['credit', 'name', 'stock', 'brand'];
    const sortField = validSortFields.includes(sort) ? sort : 'credit';
    const sortOrder = order === 'desc' ? -1 : 1;

    result.sort((a, b) => {
      let aVal = a[sortField];
      let bVal = b[sortField];

      // Для числовых полей
      if (sortField === 'credit' || sortField === 'stock') {
        aVal = parseFloat(aVal || 0);
        bVal = parseFloat(bVal || 0);
      } else {
        // Для строковых полей
        aVal = (aVal || '').toLowerCase();
        bVal = (bVal || '').toLowerCase();
      }

      if (aVal < bVal) return -1 * sortOrder;
      if (aVal > bVal) return 1 * sortOrder;
      return 0;
    });

    // Сохраняем общее количество ДО пагинации
    const total = result.length;

    // 7. Пагинация
    const limitNum = Math.min(Math.max(parseInt(limit) || 50, 1), 200);
    const offsetNum = Math.max(parseInt(offset) || 0, 0);

    const paginatedResult = result.slice(offsetNum, offsetNum + limitNum);
    const hasMore = offsetNum + limitNum < total;

    // 8. Формируем стандартный ответ
    const response = {
      success: true,
      data: paginatedResult,
      meta: {
        total: total,
        returned: paginatedResult.length,
        limit: limitNum,
        offset: offsetNum,
        has_more: hasMore,
        next_offset: hasMore ? offsetNum + limitNum : null
      },
      filters_applied: Object.keys(filtersApplied).length > 0 ? filtersApplied : null
    };

    res.status(200).json(response);

  } catch (error) {
    console.error('API Error:', error);
    res.status(500).json({
      success: false,
      error: {
        message: 'Internal server error',
        details: error.message
      },
      data: [],
      meta: {
        total: 0,
        returned: 0,
        limit: 0,
        offset: 0,
        has_more: false,
        next_offset: null
      }
    });
  }
}
