# nishtyak  фронтенд

Веб-интерфейс для подготовки Python-проектов: загрузка ZIP/`.py`, анализ зависимостей и точки входа, сборка безопасного архива, совместная работа команды.

**Стек:** Vue 3 (Composition API, `<script setup>`) · TypeScript · Vite · Pinia · Vue Router
**Шрифты:** Inter (основной текст) и TikTok Sans (заголовки, цифры) — Google Fonts.

## Запуск

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # проверка типов + production-сборка
npm run typecheck
```

По умолчанию приложение работает на моковых данных. Чтобы подключить бэкенд, создайте `.env.local`:

```env
VITE_API_URL=https://api.example.com
VITE_USE_MOCK_API=false
```

## Архитектура — Feature-Sliced Design

Код разделён на слои. Слой может импортировать **только из слоёв ниже**, а каждый слайс наружу отдаёт только свой `index.ts` (public API).

```
src/
├── app/        инициализация: main.ts, роутер, глобальные стили, layout
├── pages/      страницы — собирают виджеты под конкретный роут
│   ├── upload/          /upload
│   ├── team-projects/   /team/projects
│   └── team-invite/     /team/invite
├── widgets/    крупные самостоятельные блоки интерфейса
│   ├── app-header, app-nav, app-footer, how-it-works, team-tabs
│   ├── upload-wizard/   мастер «Загрузка → Анализ → Архив» (+ Pinia-стор шага)
│   ├── projects-table/  таблица проектов команды
│   └── invite-panel/    карточки приглашения
├── features/   пользовательские действия
│   ├── upload-project/  дропзона, валидация, загрузка
│   ├── refine-analysis/ уточнение пакетов
│   ├── archive-actions/ сборка и скачивание ZIP
│   ├── project-search/  поиск по названию
│   ├── project-sort/    сортировка по дате
│   ├── invite-member/   ссылка-приглашение: копирование, обновление, роль
│   └── workspace-switch/ переключение рабочей области
├── entities/   бизнес-сущности: типы, API, сторы, UI-«атомы»
│   ├── project/  Project, ProjectAnalysis, useProjectStore, projectApi, бейджи
│   ├── team/     Team, TeamInvite, useTeamStore, teamApi, роли
│   └── user/     текущий пользователь, аватар
└── shared/     переиспользуемое без бизнес-логики
    ├── ui/      BaseButton, BaseCard, BaseSelect, SegmentedControl, BaseIcon
    ├── lib/     форматирование, clipboard, click-outside, download
    ├── api/     HTTP-клиент
    └── config/  имена роутов, константы
```

Внутри слайса — сегменты: `ui/` (компоненты), `model/` (сторы, composables), `api/`, `lib/`, `config/`.

### Слой API

У каждой сущности есть интерфейс (`ProjectApi`, `TeamApi`) и две реализации — `*.http.ts` (реальный бэкенд) и `*.mock.ts` (in-memory). Выбор делается в `entities/*/api/index.ts` по `VITE_USE_MOCK_API`, поэтому остальной код от источника данных не зависит.

Ожидаемые эндпоинты описаны в `entities/project/api/projectApi.http.ts` и `entities/team/api/teamApi.http.ts`.

### Языки

Интерфейс на русском и английском (`vue-i18n`). Словари — `src/shared/i18n/locales/ru.ts` (источник ключей) и `en.ts`: английский типизирован по русскому, поэтому пропущенный перевод не пройдёт `npm run typecheck`. В шаблонах — `$t('ключ')`, в сторах и lib — `t` из `@/shared/i18n`. Сторы хранят ошибки ключами, текст подставляет компонент. Язык переключается в шапке, запоминается в браузере; по умолчанию — язык браузера.

### Дизайн-токены

Цвета, радиусы, шрифты и отступы — CSS-переменные в `src/app/styles/tokens.css`. Компоненты используют только токены, без «магических» значений цвета.
