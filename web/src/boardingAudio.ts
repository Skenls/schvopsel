import { useEffect, useRef, useState } from 'react'

export function useBoardingAudio(allowed: boolean) {
  const player = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!allowed) return
    const audio = new Audio('/audio/boarding.m4a')
    audio.loop = true
    player.current = audio
    const started = () => setPlaying(true)
    const stopped = () => setPlaying(false)
    const unavailable = () => {
      setPlaying(false)
      setFailed(true)
    }
    audio.addEventListener('playing', started)
    audio.addEventListener('pause', stopped)
    audio.addEventListener('error', unavailable)
    void audio.play().catch(() => {})
    return () => {
      audio.removeEventListener('playing', started)
      audio.removeEventListener('pause', stopped)
      audio.removeEventListener('error', unavailable)
      audio.pause()
      player.current = null
      setPlaying(false)
      setFailed(false)
    }
  }, [allowed])

  function toggle() {
    const audio = player.current
    if (!audio) return
    if (audio.paused) {
      void audio.play().catch(() => {})
    } else {
      audio.pause()
    }
  }

  return { playing, failed, toggle }
}
