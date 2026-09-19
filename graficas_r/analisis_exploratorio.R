#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(lubridate)
  library(scales)
})

base_url <- "https://pulso-transmi.72-60-245-2.sslip.io"
root <- "graficas_r"
data_dir <- file.path(root, "data")
output_dir <- file.path(root, "outputs")
dir.create(data_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

files <- c("stations.csv", "observations.csv", "context.csv")
for (file in files) {
  destination <- file.path(data_dir, file)
  download.file(
    url = paste0(base_url, "/v1/downloads/", file),
    destfile = destination,
    mode = "wb",
    quiet = TRUE
  )
}

stations <- read.csv(file.path(data_dir, "stations.csv"),
                     colClasses = "character", fileEncoding = "UTF-8")
observations <- read.csv(file.path(data_dir, "observations.csv"),
                         colClasses = c("character", "character", "numeric"),
                         fileEncoding = "UTF-8") %>%
  mutate(observed_at = ymd_hms(observed_at, tz = "America/Bogota"),
         station_id = as.character(station_id))
context <- read.csv(file.path(data_dir, "context.csv"),
                    fileEncoding = "UTF-8") %>%
  mutate(observed_at = ymd_hms(observed_at, tz = "America/Bogota"))

tm <- c(red = "#D71920", yellow = "#F4C300", blue = "#005EB8",
        dark = "#263238", gray = "#607D8B", light = "#ECEFF1")
theme_tm <- function() {
  theme_minimal(base_size = 11) +
    theme(plot.title = element_text(face = "bold", color = tm[["dark"]]),
          plot.subtitle = element_text(color = tm[["gray"]]),
          panel.grid.minor = element_blank(),
          legend.position = "bottom")
}

save_plot <- function(plot, filename, width = 11, height = 6) {
  ggsave(file.path(output_dir, filename), plot, width = width,
         height = height, dpi = 160, bg = "white")
}

# 1. Cobertura de observaciones por estación y día.
coverage <- observations %>%
  mutate(date = as.Date(observed_at)) %>%
  count(station_id, date) %>%
  left_join(stations %>% select(station_id, station_name), by = "station_id")
p1 <- ggplot(coverage, aes(date, reorder(station_name, station_id), fill = n)) +
  geom_tile() +
  scale_fill_gradient(low = tm[["light"]], high = tm[["red"]],
                      name = "Registros") +
  labs(title = "Cobertura temporal de la demanda",
       subtitle = "Cada celda debería representar 96 intervalos de 15 minutos",
       x = "Fecha", y = "Estación") + theme_tm()
save_plot(p1, "01_cobertura_temporal.png", height = 7)

# 2. Demanda total cada 15 minutos y promedio móvil de 24 horas.
series <- observations %>%
  group_by(observed_at) %>% summarise(demand = sum(demand), .groups = "drop") %>%
  arrange(observed_at) %>%
  mutate(moving_24h = as.numeric(stats::filter(demand, rep(1 / 96, 96), sides = 1)))
p2 <- ggplot(series, aes(observed_at, demand)) +
  geom_line(color = tm[["blue"]], alpha = 0.45) +
  geom_line(aes(y = moving_24h), color = tm[["red"]], linewidth = 0.9, na.rm = TRUE) +
  scale_y_continuous(labels = comma) +
  labs(title = "Demanda total del sistema", subtitle = "Azul: demanda observada; rojo: promedio móvil de 24 horas",
       x = NULL, y = "Demanda sumada") + theme_tm()
save_plot(p2, "02_demanda_total.png")

# 3. Distribución y comparación de demanda por estación.
station_summary <- observations %>%
  left_join(stations %>% select(station_id, station_name), by = "station_id")
p3 <- ggplot(station_summary, aes(reorder(station_name, demand, FUN = median), demand)) +
  geom_boxplot(fill = tm[["yellow"]], color = tm[["dark"]], outlier.alpha = 0.15) +
  coord_flip() + scale_y_continuous(labels = comma) +
  labs(title = "Distribución de la demanda por estación", x = NULL, y = "Demanda por intervalo") +
  theme_tm()
save_plot(p3, "03_distribucion_por_estacion.png", height = 7)

# 4. Perfil medio por hora y día de la semana.
profile <- observations %>%
  mutate(hour = hour(observed_at), weekday = wday(observed_at, label = TRUE,
                                                 abbr = FALSE, week_start = 1)) %>%
  group_by(weekday, hour) %>% summarise(mean_demand = mean(demand), .groups = "drop")
p4 <- ggplot(profile, aes(hour, mean_demand, color = weekday, group = weekday)) +
  geom_line(linewidth = 1) + geom_point(size = 1.2) +
  scale_color_manual(values = c(tm[["red"]], tm[["blue"]], tm[["yellow"]], tm[["gray"]],
                                "#8E44AD", "#16A085", "#E67E22")) +
  scale_x_continuous(breaks = seq(0, 23, 3)) + scale_y_continuous(labels = comma) +
  labs(title = "Perfil promedio de demanda", subtitle = "Patrón conjunto por hora y día de la semana",
       x = "Hora del día", y = "Demanda promedio", color = "Día") + theme_tm()
save_plot(p4, "04_perfil_horario_semanal.png")

# 5. Contexto: lluvia y eventos frente a demanda total por intervalo.
context_demand <- observations %>%
  group_by(observed_at) %>% summarise(demand = sum(demand), .groups = "drop") %>%
  inner_join(context, by = "observed_at") %>%
  mutate(evento = event_intensity > 0)
p5 <- ggplot(context_demand, aes(rain_mm, demand, color = evento, size = event_intensity + 0.2)) +
  geom_point(alpha = 0.55) +
  scale_color_manual(values = c(`FALSE` = tm[["blue"]], `TRUE` = tm[["red"]]),
                     labels = c(`FALSE` = "Sin evento", `TRUE` = "Con evento")) +
  scale_size_continuous(guide = "none") + scale_y_continuous(labels = comma) +
  labs(title = "Demanda, lluvia y eventos", subtitle = "El gráfico ayuda a detectar asociaciones y posibles outliers",
       x = "Lluvia (mm)", y = "Demanda total", color = NULL) + theme_tm()
save_plot(p5, "05_contexto_lluvia_eventos.png")

message("Listo: se generaron 5 gráficos en ", normalizePath(output_dir, winslash = "/"))
