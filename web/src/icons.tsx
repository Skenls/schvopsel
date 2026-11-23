import type { ReactNode } from 'react'

const paths: Record<string, ReactNode> = {
  sound: <><path d="M3 9v6h4l5 4V5L7 9H3Z"/><path d="M15 9a4 4 0 0 1 0 6m2-8a7 7 0 0 1 0 10"/></>,
  skull: <><path d="M8 20v-3a7 7 0 1 1 8 0v3H8Z"/><circle cx="9" cy="11" r="1"/><circle cx="15" cy="11" r="1"/><path d="M12 14v1m-1 3v2m2-2v2"/></>,
  flag: <path d="M4 22V3m0 1c5-5 10 5 16 0v11c-6 5-11-5-16 0"/>,
  anchor: <><circle cx="12" cy="5" r="2.5"/><path d="M12 8v13m-4-9h8M4 14v3c0 2 4 4 8 4s8-2 8-4v-3M2 16l2-2 2 2m12 0 2-2 2 2"/></>,
  deck: <><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v8m0 4v8M2 12h8m4 0h8M5 5l5.6 5.6m2.8 2.8L19 19M5 19l5.6-5.6m2.8-2.8L19 5"/></>,
  captain: <><circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5 5-2Z"/></>,
  gym: <><path d="m5 19 14-14M2 14l8 8M14 2l8 8M4 11l9 9M11 4l9 9"/></>,
  hold: <><path d="m12 2 9 5v10l-9 5-9-5V7l9-5Zm-9 5 9 5 9-5m-9 5v10M7.5 4.5l9 5"/></>,
  computers: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4m-5 0h10M7 8l2 2-2 2m5 0h4"/></>,
  bar: <><path d="M4 3h16l-8 10L4 3Zm8 10v7m-4 1h8M7 7h10"/></>,
  hammam: <><path d="M6 3c-4 4 4 5 0 9m6-9c-4 4 4 5 0 9m6-9c-4 4 4 5 0 9M3 16h18c-1 4-4 5-9 5s-8-1-9-5Z"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18"/></>,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
  pause: <><path d="M9 5v14m6-14v14"/></>,
  play: <path d="m8 5 11 7-11 7V5Z"/>,
  people: <><circle cx="9" cy="8" r="3"/><path d="M3 20v-2a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v2M16 5a3 3 0 0 1 0 6m1 2a5 5 0 0 1 4 5v2"/></>,
  moon: <path d="M20 15a8.5 8.5 0 0 1-11-11 8.5 8.5 0 1 0 11 11Z"/>,
  layers: <><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/></>,
}

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}
