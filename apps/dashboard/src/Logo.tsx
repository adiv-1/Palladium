// Palladium logo. Colours come from the theme tokens, so the mark is right in light and dark.
// 'mark' = helmet and crest only (nav, favicon sizes); 'shield' = helmet inside the shield with the
// constellation nodes (login, home, large placements).
export function Logo({variant = 'mark', size = 28, title = 'Palladium'}: {variant?: 'mark' | 'shield'; size?: number; title?: string}) {
  if (variant === 'mark') {
    return (
      <svg viewBox="0 0 64 64" width={size} height={size} role="img" aria-label={title} style={{display: 'block', flex: 'none'}}>
        <title>{title}</title>
    <g style={{fill: 'var(--red)'}}>
      <path d="M6.11 21.73A27.5 27.5 0 0 1 11.03 13.21L16.37 17.74A20.5 20.5 0 0 0 12.70 24.09Z" /><path d="M12.12 12.00A27.5 27.5 0 0 1 20.07 6.22L23.11 12.53A20.5 20.5 0 0 0 17.18 16.84Z" /><path d="M21.56 5.56A27.5 27.5 0 0 1 31.18 3.51L31.39 10.51A20.5 20.5 0 0 0 24.22 12.03Z" />
      <path transform="matrix(-1 0 0 1 64 0)" fillRule="evenodd" d="M32.7 12.4C41.6 12.4 49.6 18.8 49.6 28.6V44C49.6 50 45 55 38.4 58.4L32.7 61.2ZM35.4 30.4L45.6 27.4V33.6L41.8 39.2L40.8 53.2H36.8L35.4 40.2Z" />
    </g>
    <g style={{fill: 'var(--yellow)'}}>
      <path d="M32.82 3.51A27.5 27.5 0 0 1 42.44 5.56L39.78 12.03A20.5 20.5 0 0 0 32.61 10.51Z" /><path d="M43.93 6.22A27.5 27.5 0 0 1 51.88 12.00L46.82 16.84A20.5 20.5 0 0 0 40.89 12.53Z" /><path d="M52.97 13.21A27.5 27.5 0 0 1 57.89 21.73L51.30 24.09A20.5 20.5 0 0 0 47.63 17.74Z" />
      <path fillRule="evenodd" d="M32.7 12.4C41.6 12.4 49.6 18.8 49.6 28.6V44C49.6 50 45 55 38.4 58.4L32.7 61.2ZM35.4 30.4L45.6 27.4V33.6L41.8 39.2L40.8 53.2H36.8L35.4 40.2Z" />
    </g>
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 72 78" width={size * 72 / 78} height={size} role="img" aria-label={title} style={{display: 'block', flex: 'none'}}>
      <title>{title}</title>
      <path d="M36 2.5L66.5 13.5V40C66.5 58 52.5 69 36 75C19.5 69 5.5 58 5.5 40V13.5Z" fill="none" strokeWidth="2.6" strokeLinejoin="round" style={{stroke: 'var(--red)'}} />
      <g style={{stroke: 'var(--yellow)'}} strokeWidth="0.9" opacity="0.8" fill="none">
        <path d="M12 27L19 24M61 28L54 31M14 50L19 54" />
      </g>
      <g style={{fill: 'var(--yellow)'}}><circle cx="12" cy="27" r="1.7" /><circle cx="61" cy="28" r="1.7" /><circle cx="14" cy="50" r="1.7" /><circle cx="36" cy="70" r="1.5" /></g>
      <g transform="translate(4 8)">
    <g style={{fill: 'var(--red)'}}>
      <path d="M6.11 21.73A27.5 27.5 0 0 1 11.03 13.21L16.37 17.74A20.5 20.5 0 0 0 12.70 24.09Z" /><path d="M12.12 12.00A27.5 27.5 0 0 1 20.07 6.22L23.11 12.53A20.5 20.5 0 0 0 17.18 16.84Z" /><path d="M21.56 5.56A27.5 27.5 0 0 1 31.18 3.51L31.39 10.51A20.5 20.5 0 0 0 24.22 12.03Z" />
      <path transform="matrix(-1 0 0 1 64 0)" fillRule="evenodd" d="M32.7 12.4C41.6 12.4 49.6 18.8 49.6 28.6V44C49.6 50 45 55 38.4 58.4L32.7 61.2ZM35.4 30.4L45.6 27.4V33.6L41.8 39.2L40.8 53.2H36.8L35.4 40.2Z" />
    </g>
    <g style={{fill: 'var(--yellow)'}}>
      <path d="M32.82 3.51A27.5 27.5 0 0 1 42.44 5.56L39.78 12.03A20.5 20.5 0 0 0 32.61 10.51Z" /><path d="M43.93 6.22A27.5 27.5 0 0 1 51.88 12.00L46.82 16.84A20.5 20.5 0 0 0 40.89 12.53Z" /><path d="M52.97 13.21A27.5 27.5 0 0 1 57.89 21.73L51.30 24.09A20.5 20.5 0 0 0 47.63 17.74Z" />
      <path fillRule="evenodd" d="M32.7 12.4C41.6 12.4 49.6 18.8 49.6 28.6V44C49.6 50 45 55 38.4 58.4L32.7 61.2ZM35.4 30.4L45.6 27.4V33.6L41.8 39.2L40.8 53.2H36.8L35.4 40.2Z" />
    </g>
      </g>
    </svg>
  );
}
