# Radar de Mercados

Dashboard técnico de ETFs, sectores e índices. Una sola página, sin dependencias externas.

**En vivo:** https://camiloespinosam8.github.io/radar-mercados/

## Qué hace

- **Termómetro de mercado** — score 0-100 sobre seis pilares: tendencia, amplitud, volatilidad, crédito, tasas largas y apetito cíclico vs. defensivo.
- **Lectura técnica del día** — el motor rankea los ETFs y propone el primero del ranking, un táctico y de qué alejarse.

## Metodología, y por qué es así

El motor se auditó contra la literatura y contra los propios datos. Tres cosas se corrigieron después de medirlas:

**El ranking ya no castiga la fuerza.** La primera versión restaba puntaje por distancia sobre la media de 50
y por retorno anual alto. Backtesteado sobre este mismo universo daba 9,4% anual contra 15,1% de la fórmula
actual, y por debajo de simplemente comprar el índice. Peor: sobre 5,24% de distancia a la media de 50, cada
unidad extra de fuerza *bajaba* el puntaje, y a 20% el castigo superaba el techo positivo de todo el modelo.
Era la señal de Avramov-Kaplanski-Subrahmanyam (2021) con el signo invertido. Hoy el ranking es momentum
multi-horizonte 13612W (Keller & Keuning) con un filtro binario de tendencia absoluta: sin estar sobre la
media de 200 y con retorno anual positivo, un instrumento no compite.

**La volatilidad salió del puntaje y pasó al tamaño.** Antes premiaba ser aburrido. Ahora define el peso
sugerido (proporcional a 1/volatilidad), que es donde corresponde controlar el riesgo.

**El pilar de amplitud estaba invertido.** Medía "equiponderado menos índice en el año", que daba 98,7/100 en
2022 — el peor año desde 2008 — y 0/100 en 2023 y 2024, dos años de +25%. El defecto es estructural: cuando
las mega-caps lideran la caída, el equiponderado gana y el pilar lo leía como salud. Hoy mide participación
real: qué porcentaje de los ETFs de acciones de EEUU está sobre su propia media de 200.

**El pilar de crédito medía tasas, no crédito.** Comparaba HYG (duración ~3,0 años) contra LQD (~8,0). Esa
brecha de 5 años hacía que una baja de 50 puntos base de tasas moviera el pilar 32 puntos sin que se moviera
un solo punto base de spread. Hoy compara contra Treasuries de duración parecida.

**El pilar de apetito estaba clavado en 100.** Incluía energía, que es un trade de inflación y no del ciclo:
en 2022 el sector subió 59% mientras el índice caía 18,6%, y el pilar leía euforia. Hoy promedia cuatro pares
ofensivo/defensivo y energía no está entre ellos.

**Las alertas van en sigmas, no en porcentaje fijo.** Un 4% fijo disparaba unas 218 alertas al año y
significaba cosas distintas según el instrumento. Hoy el umbral es 4 sigmas de la volatilidad de cada uno, y
los cruces de media llevan banda de histéresis de ±2% — sin ella hay unos 8 cruces por ETF por año, que es
ruido alrededor de una línea, no señal de tendencia.

**Lo que todavía falta.** Los pilares se normalizan con constantes lineales, no con z-scores sobre historia
larga, porque el histórico recién arranca. El script ya guarda los valores crudos de cada pilar para poder
recalcular hacia atrás cuando haya suficiente muestra.
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
