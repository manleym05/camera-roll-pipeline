import { useEffect, useState } from 'react'
import Collections from './Collections'
import EventList from './EventList'
import EventView from './EventView'
import Sprint from './Sprint'

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const on = () => setHash(window.location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash
}

let listScroll = 0

export default function App() {
  const hash = useHash()
  const match = hash.match(/^#\/event\/([0-9a-f-]+)/)
  const eventId = match?.[1]
  // Events open at the top; going back returns to where you were in the list.
  useEffect(() => {
    const sprint = hash.startsWith('#/sprint') || hash.startsWith('#/collections')
    if (eventId || sprint) window.scrollTo(0, 0)
    else requestAnimationFrame(() => window.scrollTo(0, listScroll))
    if (eventId || sprint) return
    const on = () => { listScroll = window.scrollY }
    window.addEventListener('scroll', on, { passive: true })
    return () => window.removeEventListener('scroll', on)
  }, [eventId, hash])
  if (eventId) return <EventView id={eventId} key={eventId} />
  if (hash.startsWith('#/collections')) return <Collections />
  return hash.startsWith('#/sprint') ? <Sprint /> : <EventList />
}
