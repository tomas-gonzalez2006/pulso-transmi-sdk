#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(lubridate)
  library(scales)
  library(sf)
})

base_url <- "https://pulso-transmi.72-60-245-2.sslip.io"
root <- "graficas_r"
data_dir <- file.path(root, "data")
output_dir <- file.path(root, "outputs_v2")
dir.create(data_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

# Límites oficiales de localidades de Bogotá (GeoJSON distrital, IDECA).
boundary_url <- paste0(
  "https://datosabiertos.bogota.gov.co/dataset/28e20089-1c6d-40e1-adfb-",
  "f26cbcab7cfa/resource/fbe96995-5695-417c-81a8-bd4f8801c7f0/",
  "download/localidadesactivas12_2025.geojson"
)
boundary_file <- file.path(data_dir, "bogota_localidades.geojson")
download.file(boundary_url, boundary_file, mode = "wb", quiet = TRUE)
bogota <- st_read(boundary_file, quiet = TRUE) %>% st_transform(4326)

for (file in c("stations.csv", "observations.csv", "context.csv")) {
  download.file(paste0(base_url, "/v1/downloads/", file),
                file.path(data_dir, file), mode = "wb", quiet = TRUE)
}

stations <- read.csv(file.path(data_dir, "stations.csv"),
                     colClasses = "character", fileEncoding = "UTF-8") %>%
  mutate(latitude = as.numeric(latitude), longitude = as.numeric(longitude))
observations <- read.csv(file.path(data_dir, "observations.csv"),
                         colClasses = c("character", "character", "numeric"),
                         fileEncoding = "UTF-8") %>%
  mutate(observed_at = ymd_hms(observed_at, tz = "America/Bogota"),
         station_id = as.character(station_id),
         date = as.Date(observed_at), hour = hour(observed_at),
         weekday = wday(observed_at, label = TRUE, abbr = FALSE, week_start = 1))
context <- read.csv(file.path(data_dir, "context.csv"), fileEncoding = "UTF-8") %>%
  mutate(observed_at = ymd_hms(observed_at, tz = "America/Bogota"))

tm <- c(red = "#D71920", yellow = "#F4C300", blue = "#005EB8",
        dark = "#263238", gray = "#607D8B", light = "#ECEFF1")
theme_tm <- function() theme_minimal(base_size = 11) + theme(
  plot.title = element_text(face = "bold", color = tm[["dark"]]),
  plot.subtitle = element_text(color = tm[["gray"]]),
  panel.grid.minor = element_blank(), legend.position = "bottom")
save_plot <- function(p, file, width = 11, height = 6) {
  ggsave(file.path(output_dir, file), p, width = width, height = height,
         dpi = 170, bg = "white")
}

# 1. Perfil horario: promedio de personas por intervalo de 15 minutos.
hourly <- observations %>% group_by(hour) %>%
  summarise(mean_demand = mean(demand), .groups = "drop")
p1 <- ggplot(hourly, aes(hour, mean_demand)) +
  geom_col(fill = tm[["blue"]], width = 0.8) +
  geom_line(color = tm[["red"]], linewidth = 1) +
  geom_point(color = tm[["red"]], size = 2) +
  scale_x_continuous(breaks = 0:23) + scale_y_continuous(labels = comma) +
  labs(title = "Perfil horario de demanda", subtitle = "Promedio de personas por intervalo de 15 minutos, agrupado por hora",
       x = "Hora del día", y = "Personas promedio") + theme_tm()
save_plot(p1, "01_perfil_horario.png")

# 2. Mapa real de Bogotá: tamaño y color representan el promedio diario.
daily_station <- observations %>% group_by(date, station_id) %>%
  summarise(daily_people = sum(demand), .groups = "drop")
station_mean <- daily_station %>% group_by(station_id) %>%
  summarise(mean_daily_people = mean(daily_people), .groups = "drop") %>%
  left_join(stations, by = "station_id")
stations_sf <- st_as_sf(station_mean, coords = c("longitude", "latitude"),
                        crs = 4326, remove = FALSE)
p2 <- ggplot() +
  geom_sf(data = bogota, fill = tm[["light"]], color = "white", linewidth = 0.35) +
  geom_sf(data = stations_sf, aes(size = mean_daily_people, color = mean_daily_people),
          alpha = 0.9) +
  geom_sf_text(data = stations_sf, aes(label = station_name), size = 2.8,
               nudge_y = 0.006, color = tm[["dark"]], check_overlap = TRUE) +
  scale_color_gradient(low = tm[["yellow"]], high = tm[["red"]],
                       labels = comma, name = "Promedio diario") +
  scale_size_continuous(labels = comma, name = "Promedio diario") +
  coord_sf(datum = NA) +
  labs(title = "Mapa del promedio diario por estación",
       subtitle = "El tamaño y el color muestran personas promedio por día",
       x = "Longitud", y = "Latitud") + theme_tm()
save_plot(p2, "02_mapa_promedio_diario_estacion.png", height = 8)

# 3. Ranking: estaciones más usadas por promedio diario.
ranking <- station_mean %>% arrange(desc(mean_daily_people)) %>%
  mutate(station_name = factor(station_name, levels = rev(station_name)))
p3 <- ggplot(ranking, aes(station_name, mean_daily_people,
                          fill = mean_daily_people)) +
  geom_col() + coord_flip() +
  geom_text(aes(label = comma(round(mean_daily_people))), hjust = -0.1,
            size = 3.4, color = tm[["dark"]]) +
  scale_fill_gradient(low = tm[["yellow"]], high = tm[["red"]], guide = "none") +
  scale_y_continuous(labels = comma, expand = expansion(mult = c(0, 0.12))) +
  labs(title = "Estaciones más usadas", subtitle = "Ordenadas por promedio diario de personas",
       x = NULL, y = "Personas promedio por día") + theme_tm()
save_plot(p3, "03_ranking_estaciones.png", height = 7)

# 4. Línea de promedio por día: media entre las 12 estaciones.
daily_mean <- daily_station %>% group_by(date) %>%
  summarise(mean_station_people = mean(daily_people), total_people = sum(daily_people),
            .groups = "drop") %>% arrange(date) %>%
  mutate(rolling_7d = as.numeric(stats::filter(mean_station_people, rep(1 / 7, 7), sides = 1)))
p4 <- ggplot(daily_mean, aes(date, mean_station_people)) +
  geom_line(color = tm[["blue"]], linewidth = 0.8) +
  geom_line(aes(y = rolling_7d), color = tm[["red"]], linewidth = 1.1, na.rm = TRUE) +
  scale_y_continuous(labels = comma) +
  labs(title = "Promedio diario a través del tiempo",
       subtitle = "Azul: promedio diario; rojo: promedio móvil de 7 días",
       x = "Día", y = "Personas promedio por estación") + theme_tm()
save_plot(p4, "04_promedio_por_dia.png")

# 5. Heatmap del promedio por hora y día de la semana.
week_profile <- observations %>% group_by(weekday, hour) %>%
  summarise(mean_demand = mean(demand), .groups = "drop")
p5 <- ggplot(week_profile, aes(hour, weekday, fill = mean_demand)) +
  geom_tile(color = "white", linewidth = 0.2) +
  scale_fill_gradient(low = tm[["light"]], high = tm[["red"]], labels = comma,
                      name = "Personas") + scale_x_continuous(breaks = seq(0, 23, 2)) +
  labs(title = "Mapa de calor: hora y día de la semana",
       subtitle = "Permite identificar horas punta y diferencias entre días",
       x = "Hora del día", y = "Día") + theme_tm()
save_plot(p5, "05_heatmap_hora_dia.png")

# 6. Evolución de lluvia y eventos junto a la demanda diaria.
daily_context <- observations %>% group_by(date) %>%
  summarise(mean_demand = mean(demand), .groups = "drop") %>%
  left_join(context %>% mutate(date = as.Date(observed_at)) %>% group_by(date) %>%
              summarise(rain_mm = sum(rain_mm), event_intensity = sum(event_intensity),
                        .groups = "drop"), by = "date")
p6 <- ggplot(daily_context, aes(date, mean_demand)) +
  geom_line(color = tm[["blue"]], linewidth = 0.8) +
  geom_point(aes(color = event_intensity > 0, size = rain_mm + 0.1), alpha = 0.8) +
  scale_color_manual(values = c(`FALSE` = tm[["gray"]], `TRUE` = tm[["red"]]),
                     labels = c(`FALSE` = "Sin evento", `TRUE` = "Con evento"), name = NULL) +
  scale_size_continuous(name = "Lluvia diaria (mm)") + scale_y_continuous(labels = comma) +
  labs(title = "Promedio diario y contexto externo",
       subtitle = "El color marca días con eventos; el tamaño representa lluvia acumulada",
       x = "Día", y = "Personas promedio por intervalo") + theme_tm()
save_plot(p6, "06_demanda_contexto_diario.png")

message("Listo: se generaron 6 gráficos en ", normalizePath(output_dir, winslash = "/"))
