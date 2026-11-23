export type Mode = 'demo' | 'prod'

declare global {
  interface Window {
    Telegram?: { WebApp: { initData: string; ready(): void } }
  }
}

export async function telegramInitData(): Promise<string> {
  if (!window.Telegram?.WebApp) {
    await new Promise<void>((resolve, reject) => {
      const script = document.createElement('script')
      script.src = 'https://telegram.org/js/telegram-web-app.js'
      script.onload = () => resolve()
      script.onerror = () => reject(new Error('Не удалось загрузить Telegram'))
      document.head.append(script)
    })
  }
  const app = window.Telegram?.WebApp
  app?.ready()
  return app?.initData ?? ''
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

export type Api = <T>(path: string, options?: RequestInit) => Promise<T>

export function makeApi(mode: Mode, initData: string, profile: string): Api {
  return async (path, options = {}) => {
    const headers = new Headers(options.headers)
    if (mode === 'demo') headers.set('x-demo-profile', profile)
    else headers.set('x-telegram-init-data', initData)
    if (options.body) headers.set('Content-Type', 'application/json')
    const response = await fetch(`/api/${path}`, { ...options, headers, cache: 'no-store' })
    const data = await response.json()
    if (!response.ok) {
      const message = typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос'
      throw new ApiError(response.status, message)
    }
    return data
  }
}
