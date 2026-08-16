# Radar de Mercados

Dashboard técnico de ETFs, sectores e índices. Una sola página, sin dependencias externas.

**En vivo:** https://camiloespinosam8.github.io/radar-mercados/

## Qué hace

- **Termómetro de mercado** — score 0-100 sobre seis pilares: tendencia, amplitud, volatilidad, crédito, tasas largas y apetito cíclico vs. defensivo.
- **Lectura técnica del día** — el motor rankea 46 instrumentos y propone el largo más limpio, un táctico y de qué alejarse. Premia estructura y castiga sobre-extensión, volatilidad y cruces bajistas.
- **Noticias con análisis de impacto** — titulares curados de CNBC, Yahoo Finance, XTB, Reuters y LarrainVial, cada uno con qué significa para la decisión.
- **Contador de menciones** — cuántos titulares toca cada instrumento, para ver dónde se concentra la novedad. Detecta divergencias: mucho ruido con poco movimiento, y movimientos fuertes sin cobertura.
- **Titulares en vivo** — feeds RSS con alerta automática cuando la noticia toca un instrumento del radar.
- **Mapa de rotación** — cuadrante de tendencia larga vs. momentum corto.
- **Radar completo** — tabla ordenable con veredicto separado de largo y corto plazo.
- **Chile** — IPSA, cartera recomendada de LarrainVial y la brecha entre comprar Chile en pesos o en dólares.

## Datos

Precios y series históricas de Yahoo Finance (251 sesiones por instrumento, cierres ajustados). El botón
**Actualizar en vivo** vuelve a bajar todo y recalcula medias móviles, momentum, termómetro y el pick del día.
Como los navegadores bloquean las llamadas directas por CORS, las peticiones pasan por `r.jina.ai`.
Si el proveedor limita el tráfico, la página queda con el último snapshot válido y lo avisa en el encabezado.

## Aviso

Esto es una lectura técnica y de contexto de mercado construida con reglas explícitas sobre datos públicos.
No es asesoría de inversión, no considera situación financiera ni objetivos de nadie, y las señales técnicas
fallan con frecuencia. Para decisiones reales de plata, la conversación va con un asesor con licencia.
