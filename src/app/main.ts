import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import { i18n } from '@/shared/i18n'
import { router } from './router'
import './styles/tokens.css'
import './styles/base.css'

const app = createApp(App).use(createPinia()).use(i18n).use(router)

router.isReady().then(() => app.mount('#app'))
