import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { Api } from './access'
import { vessels } from './fleet'
import type { Crew } from './fleet'
import { Icon } from './icons'

type Member = Crew & { hidden: boolean; telegram_id?: number | null; allowed?: boolean; is_admin?: boolean }

function MemberForm({ member, main, api }: { member: Member; main: boolean; api: Api }) {
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const id = String(form.get('telegram') ?? '').trim()
    const fields = main ? {
      telegram_id: id ? Number(id) : null,
      allowed: form.has('allowed'),
      is_admin: form.has('admin'),
    } : {}
    setSaving(true)
    setMessage('')
    try {
      await api(`admin/crew/${member.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ vessel: form.get('vessel'), hidden: form.has('hidden'), ...fields }),
      })
      setMessage('Сохранено')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Не удалось сохранить')
    } finally {
      setSaving(false)
    }
  }

  return <form className="member-form" aria-labelledby={`member-form-${member.id}`} onSubmit={submit}>
    <fieldset disabled={saving}>
      <legend id={`member-form-${member.id}`}>{member.name}</legend>
      <label>Судно<select name="vessel" defaultValue={member.vessel}>
        {vessels.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select></label>
      <label className="permission"><input type="checkbox" name="hidden" defaultChecked={member.hidden} /> Скрыт из команды</label>
      {main && <>
        <label>Telegram ID<input name="telegram" type="text" inputMode="numeric" pattern="[0-9]{1,16}" maxLength={16} spellCheck={false} defaultValue={member.telegram_id ?? ''} /></label>
        <label className="permission"><input type="checkbox" name="allowed" defaultChecked={member.allowed} /> Белый список</label>
        <label className="permission"><input type="checkbox" name="admin" defaultChecked={member.is_admin} /> Админ</label>
      </>}
      <button type="submit" className="small-action">Сохранить</button>
    </fieldset>
    <p className="save-status" role="status">{message}</p>
  </form>
}

export function AdminPanel({ api, mainId }: { api: Api; mainId?: number }) {
  const [members, setMembers] = useState<Member[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    api<Member[]>('admin/crew', { signal: controller.signal }).then(setMembers).catch(error => {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : 'Нет связи')
    })
    return () => controller.abort()
  }, [api])
  return <section id="team-admin" className="admin-panel" aria-labelledby="admin-title">
    <h2 id="admin-title"><Icon name="flag" size={21} /> Управление командой</h2>
    {error && <p role="alert">{error}</p>}
    {members.map(member => <MemberForm key={member.id} member={member} main={mainId !== undefined && member.id !== mainId} api={api} />)}
  </section>
}
