/**
 * Following a reply in a scroll box as it is written -- and letting go the
 * moment the reader scrolls up.
 *
 * Reading "the reader scrolled up" from where the box stands lost a race: a
 * scroll event arrives a frame after the scroll, and with tokens coming every
 * few milliseconds the next one had already put the box back at the bottom.
 * So a reply or its thinking still could not be scrolled away from (seen on
 * the release VM after aefd9e1). Intent is read from the input instead --
 * wheel, touch, keys, the scrollbar -- which arrives before the scroll it
 * causes. Following resumes once the reader is back at the bottom.
 *
 * Plain DOM, no React: the browser test loads it on its own.
 */

export interface Follower {
  /** Whether new content is followed. */
  following(): boolean;
  /** After the content changed: back to the bottom, when following. */
  keepUp(): void;
  /** Follow again, from the bottom (the reader sent something, or asked to). */
  follow(): void;
  dispose(): void;
}

/** This close to the bottom still counts as reading the latest. */
export const FOLLOW_SLACK_PX = 32;

const UP_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);

export function followScroll(
  box: HTMLElement,
  onChange: (following: boolean) => void,
  slackPx = FOLLOW_SLACK_PX,
): Follower {
  let following = true;
  let lastTop = box.scrollTop;
  let touchY: number | null = null;

  const set = (value: boolean) => {
    if (value === following) return;
    following = value;
    onChange(value);
  };
  const atBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight <= slackPx;
  const letGo = () => set(false);

  const onWheel = (event: WheelEvent) => {
    if (event.deltaY < 0) letGo();
  };
  const onTouchStart = (event: TouchEvent) => {
    touchY = event.touches[0]?.clientY ?? null;
  };
  const onTouchMove = (event: TouchEvent) => {
    const y = event.touches[0]?.clientY;
    if (y === undefined) return;
    // The finger going down drags the content down: reading back up.
    if (touchY !== null && y > touchY + 2) letGo();
    touchY = y;
  };
  const onKeyDown = (event: KeyboardEvent) => {
    if (UP_KEYS.has(event.key)) letGo();
  };
  const onPointerDown = (event: PointerEvent) => {
    // On the box itself, right of its content: the scrollbar.
    if (event.target === box && event.offsetX >= box.clientWidth) letGo();
  };
  const onScroll = () => {
    const top = box.scrollTop;
    // Up, and not just held at the bottom of content that got shorter.
    if (top < lastTop - 1 && !atBottom()) letGo();
    else if (top >= lastTop && atBottom()) set(true);
    lastTop = top;
  };

  box.addEventListener("wheel", onWheel, { passive: true });
  box.addEventListener("touchstart", onTouchStart, { passive: true });
  box.addEventListener("touchmove", onTouchMove, { passive: true });
  box.addEventListener("keydown", onKeyDown);
  box.addEventListener("pointerdown", onPointerDown);
  box.addEventListener("scroll", onScroll, { passive: true });

  const keepUp = () => {
    if (!following) return;
    box.scrollTop = box.scrollHeight;
    lastTop = box.scrollTop;
  };

  return {
    following: () => following,
    keepUp,
    follow: () => {
      set(true);
      keepUp();
    },
    dispose: () => {
      box.removeEventListener("wheel", onWheel);
      box.removeEventListener("touchstart", onTouchStart);
      box.removeEventListener("touchmove", onTouchMove);
      box.removeEventListener("keydown", onKeyDown);
      box.removeEventListener("pointerdown", onPointerDown);
      box.removeEventListener("scroll", onScroll);
    },
  };
}
