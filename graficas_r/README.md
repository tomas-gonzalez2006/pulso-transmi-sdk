# Gráficas exploratorias en R

Este directorio contiene un análisis exploratorio reproducible de la API de
Pulso TransMi. El script descarga `stations.csv`, `observations.csv` y
`context.csv`, y guarda los gráficos en `graficas_r/outputs/`.

## Ejecución

Se requiere R y los paquetes `ggplot2`, `dplyr`, `tidyr`, `lubridate` y
`scales`:

```r
install.packages(c("ggplot2", "dplyr", "tidyr", "lubridate", "scales", "sf"))
source("graficas_r/analisis_exploratorio.R")
```

Los archivos descargados quedan en `graficas_r/data/`. La paleta usada está
inspirada en TransMilenio: rojo, amarillo, azul y grises neutros.
