export const rooms = [
  ['deck', 'Палуба'],
  ['captain', 'Капитанская рубка'],
  ['gym', 'Тренажёрный зал'],
  ['hold', 'Трюм'],
  ['computers', 'Компьютерный клуб'],
  ['bar', 'Бар с ромом'],
  ['hammam', 'Хамам'],
] as const

export const vessels = [
  ['ship', 'Хамамтон'],
  ['dinghy', 'Гнилая шлюпка'],
] as const

export type Room = (typeof rooms)[number][0]
export type Vessel = (typeof vessels)[number][0]
export type Location = { vessel: Vessel; room: Room }
export type Crew = Location & { id: number; name: string }
export type Viewer = Crew & { admin: boolean; mainAdmin: boolean }

export const roomName = (id: Room) => rooms.find(([key]) => key === id)?.[1]
export const sprite = (id: number) => `/crew/${id}.webp`
