import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiError, makeApi, telegramInitData } from './access'
import type { Mode } from './access'
import { AdminPanel } from './admin'
import { useBoardingAudio } from './boardingAudio'
import { rooms, roomName, sprite, vessels } from './fleet'
import type { Crew, Location, Room, Vessel, Viewer } from './fleet'
import { Icon } from './icons'
import './style.css'

type LessonVariant = { subgroup: number; subject: string; auditorium: string; teacher: string }
type Lesson = { kind: 'lesson'; number: number; subject: string; auditorium: string; start: string; end: string; variants?: LessonVariant[] }
type Break = { kind: 'break'; minutes: number; start: string; end: string }
type Period = Lesson | Break
type ScheduleDay = { date: string; available: boolean; lessons: Lesson[] }
type Schedule = ScheduleDay & { timezone: string; source: 'demo' | 'telegram'; nextDay: ScheduleDay | null; current: Period | null; next: Period | null; deadline: number | null }
type Snapshot = { me: Viewer; serverNow: number; crew: Crew[]; profiles: Pick<Crew, 'id' | 'name'>[]; schedule: Schedule }

const dateLabel = new Intl.DateTimeFormat('ru', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' })
const shipClock = new Intl.DateTimeFormat('ru', { timeZone: 'Europe/Minsk', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

function roomLabel(room: string) {
  return room === 'ч/з' ? 'Читальный зал' : room ? `Каб. ${room}` : 'Без кабинета'
}

function lessonRooms(lesson: Lesson) {
  if (!lesson.variants?.some(variant => variant.subgroup)) return roomLabel(lesson.auditorium)
  return lesson.variants.map(variant => `${variant.subgroup}-я: ${roomLabel(variant.auditorium)}`).join(' · ')
}

function Countdown({ deadline, now }: { deadline: number; now: number }) {
  const seconds = Math.max(0, Math.ceil((deadline - now) / 1000))
  return <>{String(Math.floor(seconds / 60)).padStart(2, '0')}:{String(seconds % 60).padStart(2, '0')}</>
}

function Voyage({ schedule, now }: { schedule?: Schedule; now: number }) {
  const current = schedule?.current
  const next = schedule?.next
  const label = current?.kind === 'lesson' ? `Плавание · Урок ${current.number}`
    : current?.kind === 'break' ? (current.minutes === 20 ? 'Длинный швопсель' : 'Короткий швопсель')
      : 'Статус плавания'
  const title = current?.kind === 'lesson' ? current.subject
    : current?.kind === 'break' ? (current.minutes === 20 ? 'Обеденный перерыв' : 'Перемена')
      : schedule ? 'Корабль вне расписания' : 'Сверяем часы…'
  const detail = current?.kind === 'lesson' ? `${lessonRooms(current)} · ${current.start} – ${current.end}`
    : current?.kind === 'break' ? `${current.start} – ${current.end}${next?.kind === 'lesson' ? ` · Далее ${next.subject.toLowerCase()}` : ''}`
      : next?.kind === 'lesson' ? `Первый урок в ${next.start} · ${next.subject}`
        : schedule ? (!schedule.available ? 'Лист на сегодня пока не загружен'
          : schedule.lessons.length ? 'Учебный день завершён' : 'Сегодня уроков нет') : ''
  return <div className="voyage">
    <div className="voyage-icon"><Icon name={current ? 'clock' : 'moon'} size={23} /></div>
    <div className="voyage-copy">
      <span className="eyebrow">{label}</span>
      <strong>{title}</strong>
      {detail && <span className="voyage-detail">{detail}</span>}
    </div>
    {schedule?.deadline && <div className="countdown"><span className="eyebrow">{current ? 'Осталось' : 'До старта'}</span><b><Countdown deadline={schedule.deadline} now={now} /></b></div>}
  </div>
}

function Figure({ member, position, width, mine }: { member: Crew; position: number; width: number; mine: boolean }) {
  const style = { '--standing-x': `${position}%`, '--sprite-max-width': `${width}cqw` } as CSSProperties
  return <div className="figure-track" style={style} aria-hidden="true" data-raised={member.id % 2 === 1}>
    <div className="figure">
      <span className={mine ? 'nametag mine' : 'nametag'}>{member.name}</span>
      <img className="sprite" src={sprite(member.id)} alt="" draggable="false" />
    </div>
  </div>
}

function Gate({ message, botUrl }: { message: string; botUrl?: string }) {
  return <main className="gate">
    <div className="brand-icon"><Icon name="skull" size={24} /></div>
    <h1>Хамамтон</h1>
    <p role="status">{message}</p>
    {botUrl && <a className="small-action" href={botUrl}><Icon name="arrow" size={17} /> Перейти в бота</a>}
  </main>
}

function ScheduleLog({ schedule, activeLesson }: { schedule: Schedule; activeLesson: number | null }) {
  const [showNext, setShowNext] = useState(false)
  const day = showNext && schedule.nextDay ? schedule.nextDay : schedule
  const current = day === schedule
  return <section className="log" aria-labelledby="log-title">
    <div className="log-heading">
      <h2 id="log-title"><Icon name="calendar" size={22} /> {current ? 'Расписание на сегодня' : 'Следующий учебный день'}</h2>
      {schedule.nextDay ? <div className="schedule-days" role="group" aria-label="День расписания">
        <button type="button" className="small-action" aria-pressed={current} onClick={() => setShowNext(false)}>Сегодня</button>
        <button type="button" className="small-action" aria-pressed={!current} onClick={() => setShowNext(true)}>Следующий день</button>
      </div> : <span className="demo">Демо-расписание</span>}
    </div>
    {!current && <p className="schedule-date">{dateLabel.format(new Date(day.date))}</p>}
    {day.lessons.length ? <ol className="lesson-list">
      {day.lessons.map(lesson => {
        const active = current && lesson.number === activeLesson
        const split = lesson.variants?.some(variant => variant.subgroup)
        return <li key={lesson.number} className={active ? 'active-lesson' : ''} aria-current={active ? 'step' : undefined}>
          <div><span>{lesson.number} урок</span><time>{lesson.start}–{lesson.end}</time></div>
          {split ? lesson.variants?.map(variant => <section className="lesson-variant" key={variant.subgroup}>
            <small>{variant.subgroup}-я подгруппа</small>
            <strong>{variant.subject}</strong>
            <small>{roomLabel(variant.auditorium)}</small>
          </section>) : <><strong>{lesson.subject}</strong><small>{roomLabel(lesson.auditorium)}</small></>}
        </li>
      })}
    </ol> : <p className="empty-schedule">{day.available ? 'Уроков нет.' : 'Лист расписания пока не загружен.'}</p>}
  </section>
}

function Board({ mode, initData, profile, botUrl, selectProfile, onAccess, soundControl }: {
  mode: Mode; initData: string; profile: string; botUrl: string
  selectProfile: (profile: string) => void; onAccess: (allowed: boolean) => void; soundControl: ReactNode
}) {
  const api = useMemo(() => makeApi(mode, initData, profile), [mode, initData, profile])
  const [view, setView] = useState<Location | null>(null)
  const [paused, setPaused] = useState(false)
  const [showAdmin, setShowAdmin] = useState(false)
  const [snapshot, setSnapshot] = useState<Snapshot>()
  const [lastSync, setLastSync] = useState(0)
  const [localTime, setLocalTime] = useState(Date.now)
  const [failed, setFailed] = useState('')
  const [denied, setDenied] = useState('')
  const [moving, setMoving] = useState(false)
  const [moveError, setMoveError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    let pending = false
    async function refresh() {
      if (document.hidden || pending || controller.signal.aborted) return
      pending = true
      try {
        const start = Date.now()
        const data = await api<Snapshot>('state', { signal: controller.signal })
        const end = Date.now()
        setSnapshot(previous => previous && previous.serverNow > data.serverNow ? previous : data)
        setLastSync((start + end) / 2)
        setLocalTime(end)
        setFailed('')
        onAccess(true)
      } catch (error) {
        if (controller.signal.aborted) return
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          setSnapshot(undefined)
          setDenied(error.message)
          onAccess(false)
          return
        }
        setFailed(error instanceof ApiError ? error.message : 'Нет связи с бортом')
      }
      finally {
        pending = false
      }
      if (!controller.signal.aborted && !document.hidden) timer = setTimeout(refresh, 5000)
    }
    function visibility() {
      clearTimeout(timer)
      if (!document.hidden) {
        setLocalTime(Date.now())
        void refresh()
      }
    }
    document.addEventListener('visibilitychange', visibility)
    void refresh()
    const clock = setInterval(() => { if (!document.hidden) setLocalTime(Date.now()) }, 1000)
    return () => {
      controller.abort()
      document.removeEventListener('visibilitychange', visibility)
      clearTimeout(timer)
      clearInterval(clock)
    }
  }, [api, onAccess])

  const myVessel = snapshot?.me.vessel
  useEffect(() => { setView(null) }, [myVessel])

  if (denied || !snapshot) return <Gate message={denied || failed || 'Поднимаемся на борт…'} botUrl={denied || failed ? botUrl : undefined} />

  const { me, schedule } = snapshot
  const { vessel, room } = view ?? me
  const dinghy = vessel === 'dinghy'
  const deck = room === 'deck'
  const availableRooms = dinghy ? rooms.slice(0, 1) : rooms
  const crew = snapshot.crew.filter(member => member.vessel === vessel)
  const visible = crew.filter(member => member.room === room)
  const now = snapshot.serverNow + localTime - lastSync
  const activeLesson = schedule.current?.kind === 'lesson' ? schedule.current.number : null

  function selectVessel(next: Vessel) {
    setMoveError('')
    setView(next === me.vessel ? null : { vessel: next, room: 'deck' })
  }

  async function selectRoom(next: Room) {
    setMoveError('')
    if (vessel !== me.vessel) {
      setView({ vessel, room: next })
      return
    }
    setMoving(true)
    try {
      await api('location', { method: 'PUT', body: JSON.stringify({ vessel, room: next }) })
      setSnapshot(current => current && ({
        ...current,
        me: current.me.vessel === vessel ? { ...current.me, room: next } : current.me,
        crew: current.crew.map(member => member.id === me.id && member.vessel === vessel ? { ...member, room: next } : member),
      }))
      setView(null)
    } catch (error) {
      setMoveError(error instanceof Error ? error.message : 'Не удалось перейти')
    } finally {
      setMoving(false)
    }
  }

  function figures() {
    const spread = deck ? (dinghy && visible.length > 2 ? 66 : 52) : visible.length > 4 ? 76 : 62
    const start = visible.length < 2 ? 50 : (100 - spread) / 2
    const spacing = visible.length < 2 ? 0 : spread / (visible.length - 1)
    const width = visible.length < 2 ? 40 : spacing * (dinghy ? .56 : .94)
    return visible.map((member, index) => <Figure key={member.id} member={member} mine={member.id === me.id} position={start + spacing * index} width={width} />)
  }

  return <>
    <header className="masthead">
      <div className="page-width header-inner">
        <a className="brand" href="/" aria-label="Хамамтон, главная">
          <span className="brand-icon"><Icon name="skull" size={24} /></span>
          <span>Хамамтон<small>Бортовой журнал</small></span>
        </a>
        <div className="header-right">
          {mode === 'demo' ? <label className="profile-control">Демо-профиль
            <select value={profile} onChange={event => selectProfile(event.target.value)}>
              {snapshot.profiles.map(member => <option key={member.id} value={member.id}>{member.name}</option>)}
            </select>
          </label> : <span className="profile-control">{me.name}</span>}
          <span className="header-clock"><Icon name="clock" size={16} /><b>{shipClock.format(now)}</b><small>Минск</small></span>
        </div>
      </div>
    </header>

    <main className="page-width">
      <div className="page-heading">
        <div className="vessel-title"><span className="pennant" aria-hidden="true"><Icon name={dinghy ? 'anchor' : 'skull'} size={20} /></span>
          <h1>{dinghy ? 'На гнилой шлюпке' : 'На борту «Хамамтона»'}</h1>
        </div>
        <div className="dateline"><Icon name="calendar" size={17} />{dateLabel.format(new Date(schedule.date))}</div>
      </div>
      <div className="vessel-bar">
        <nav className="vessel-selector" aria-label="Выбор судна">
          {vessels.map(([id, name]) => <button type="button" key={id} aria-pressed={vessel === id} onClick={() => selectVessel(id)}>
            <Icon name={id === 'ship' ? 'flag' : 'anchor'} size={17} /> {name}
          </button>)}
        </nav>
        <div className="vessel-actions">
          {soundControl}
          {me.admin && <button className="small-action" type="button" aria-expanded={showAdmin} aria-controls={showAdmin ? 'team-admin' : undefined} onClick={() => setShowAdmin(!showAdmin)}>
            <Icon name="deck" size={16} /> Управление командой
          </button>}
        </div>
      </div>
      {showAdmin && me.admin && <AdminPanel api={api} mainId={me.mainAdmin ? me.id : undefined} />}
      {failed && <p className="move-status" role="status">{failed}</p>}

      <div className="ship-layout">
        <section className={`scene ${deck ? 'at-sea' : 'in-room'} ${dinghy ? 'dinghy-scene' : ''} ${paused ? 'paused' : ''}`} aria-label={dinghy ? 'Гнилая шлюпка' : roomName(room)}>
          {deck ? <>
            <div className="sea-art" />
            <div className="sea-vignette" />
            <div className={`ship ${dinghy ? 'dinghy' : ''}`}>
              <picture>
                {!dinghy && <source media="(max-width: 760px)" srcSet="/art/ship-small.webp" />}
                <img className="ship-art" src={dinghy ? '/art/dinghy.webp' : '/art/ship.webp'} alt={dinghy ? 'Гнилая шлюпка' : 'Хамамтон под парусами'} fetchPriority="high" />
              </picture>
              <div className="figures on-deck">{figures()}</div>
            </div>
            <div className="wake wake-one" /><div className="wake wake-two" /><div className="wake wake-three" />
            <Voyage schedule={schedule} now={now} />
          </> : <>
            <img className="room-art" src={`/art/${room}.webp`} alt="" />
            <div className="sea-vignette" />
            <div className="room-heading"><span className="eyebrow">Отсек корабля</span><h2>{roomName(room)}</h2>
              {visible.length === 0 && <p>Сейчас здесь никого нет</p>}
            </div>
            <div className="figures inside">{figures()}</div>
          </>}
          <div className="scene-tools">
            <span><Icon name={room} size={17} />{roomName(room)}</span>
            {deck && <button type="button" className="motion" onClick={() => setPaused(value => !value)} aria-label={paused ? 'Возобновить анимацию' : 'Приостановить анимацию'} title={paused ? 'Возобновить анимацию' : 'Приостановить анимацию'}><Icon name={paused ? 'play' : 'pause'} size={17} /></button>}
          </div>
        </section>

        <aside className="sidebar" aria-label="Отсеки и команда">
          <section className="sidebar-panel">
            <h2><Icon name="layers" size={17} /> {dinghy ? 'На шлюпке' : 'Отсеки корабля'}</h2>
            <nav aria-label="Отсеки судна">
              {availableRooms.map(([id, name]) => <button key={id} type="button" disabled={moving} aria-current={room === id ? 'location' : undefined} onClick={() => void selectRoom(id)}>
                <Icon name={id} size={17} /><span>{name}</span>
                <small>{crew.filter(member => member.room === id).length}</small>
              </button>)}
            </nav>
            {moveError && <p className="move-status" role="alert">{moveError}</p>}
          </section>
          <section className="sidebar-panel crew-panel">
            <h2><Icon name="people" size={18} /> Команда <small>{crew.length}</small></h2>
            <ul>{crew.map(member => <li key={member.id}>
              <button type="button" onClick={() => setView({ vessel, room: member.room })} aria-label={`${member.name} — ${roomName(member.room)}`}>
                <span className="avatar"><img src={sprite(member.id)} alt="" /></span>
                <span>{member.name}{me.id === member.id && <span className="you"> · вы</span>}</span><span className="member-location">{roomName(member.room)}</span>
              </button>
            </li>)}</ul>
          </section>
        </aside>
      </div>

      <ScheduleLog schedule={schedule} activeLesson={activeLesson} />
    </main>
  </>
}

function App() {
  const [connection, setConnection] = useState<{ mode: Mode; initData: string }>()
  const [profile, setProfile] = useState('0')
  const [botUrl, setBotUrl] = useState('')
  const [error, setError] = useState('')
  const [entered, setEntered] = useState(false)
  const sound = useBoardingAudio(entered)
  useEffect(() => {
    const controller = new AbortController()
    async function start() {
      try {
        const response = await fetch('/api/config', { cache: 'no-store', signal: controller.signal })
        if (!response.ok) throw new Error('Нет связи с бортом')
        const config = await response.json() as { mode: Mode; botUrl: string }
        const { mode } = config
        setBotUrl(config.botUrl)
        if (mode === 'demo') {
          const saved = localStorage.getItem('hamamton-demo-profile') ?? ''
          setProfile(saved === 'admin' ? '6' : /^[0-6]$/.test(saved) ? saved : '0')
          setConnection({ mode, initData: '' })
          void telegramInitData().catch(() => {})
          return
        }
        const initData = await telegramInitData()
        if (!initData) throw new Error('Откройте мини-приложение из Telegram')
        setConnection({ mode, initData })
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Нет связи с бортом')
      }
    }
    void start()
    return () => controller.abort()
  }, [])
  if (!connection) return <Gate message={error || 'Поднимаемся на борт…'} botUrl={error ? botUrl : undefined} />
  const soundControl = <button type="button" className="small-action" onClick={sound.toggle} disabled={sound.failed}>
    <Icon name={sound.playing ? 'pause' : 'sound'} size={16} />
    {sound.failed ? 'Звук недоступен' : sound.playing ? 'Пауза звука' : 'Включить звук'}
  </button>
  return <Board key={profile} {...connection} profile={profile} botUrl={botUrl} soundControl={soundControl} onAccess={setEntered} selectProfile={next => {
    localStorage.setItem('hamamton-demo-profile', next)
    setProfile(next)
  }} />
}

createRoot(document.getElementById('root')!).render(<App />)
