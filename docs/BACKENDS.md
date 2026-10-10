# Inference backendek és Pi gyorsítás

## PyTorch / Ultralytics

Az opcionális `runtime` target / `docker-compose.pytorch.yml` image
`AI_BACKEND=ultralytics` és YOLO11n `.pt` modellt használ. Az adapter auto
CUDA → MPS → CPU választást támogat; a PyTorch image CPU wheel-t hoz,
Mac Dockerből MPS nem érhető el. Az alap production image már NCNN.

## Native NCNN

`docker-compose.ncnn.yml` a Dockerfile `ncnn` targetjét választja. Ugyanazt a
YOLO11n modellt exportálja 320×320-ra. Exporthoz Ultralytics/PyTorch/PNNX kell,
de ezek csak a külön `ncnn-export` builder stage-ben vannak. A végső NCNN
image csak a közös web/OpenCV függőségeket és a natív NCNN runtime-ot tartalmazza.

`NCNNDetector` a standard YOLO detect exportot kezeli: letterbox, RGB CHW
float32 bemenet, class confidence szűrés, osztályonkénti NMS (IoU 0,7), majd
visszavetített és a képhez vágott pixelkoordináták. Nincs embedded/end-to-end NMS,
segmentation vagy pose adapter. Az export metadata és inputméret ellenőrzött.

CPU-only, alapból `AI_CPU_THREADS=2`, FP32 storage/arithmetic. A confidence,
figyelt osztályok és többképes validáció ugyanazok, mint a másik backendnél.
Sem Torch, sem Ultralytics, sem PNNX nincs a végső NCNN image-ben. PyTorch-ra
visszaálláshoz a külön PyTorch image-et kell indítani, nem ugyanazt az image-et.
Az NCNN wheel OpenCV GUI dependency-jét szándékosan nem telepítjük:
`--no-deps` mellett a rögzített headless OpenCV biztosítja a `cv2` modult.

## Helyi RTSP reconnect

`docker-compose.stream.yml` a helyi gateway mellett a kamera reader reconnect várakozását
korlátozza: 1, 2, 2, 2… másodperc. Az RTSP open/read timeout külön paraméter.
Publikus feedhez az override nélkül marad 1, 2, 5, 10, 20, 30… másodperc.

## Mérés

A benchmark nem hoz létre eseményt és nem küld notificationt. Azonos videó,
időpont és pixelkivágás mellett, egy cold warmup után öt mérés mediánját adja:

```bash
docker run --rm --network none \
  -v "$PWD/data/test:/data/test:ro" -v "$PWD/tools:/app/tools:ro" \
  --entrypoint python camera-stack:ncnn /app/tools/benchmark_detector.py \
  /data/test/source.mp4 --backend ncnn --model /opt/models/yolo11n_ncnn_model \
  --crop 300 150 750 720 --iterations 5
```

A PyTorch összehasonlításhoz `camera-stack:pytorch` image és `--backend ultralytics`,
`--model /opt/models/yolo11n.pt` argumentumok használhatók.

Raspberry Pi 4, 320-as input, ugyanaz a tesztvideó 10. másodperce és kivágása,
háttérben működő kameraapp mellett:

| Backend | Medián | Öt mérés tartománya | Person confidence |
| --- | --- | --- | --- |
| PyTorch | 2001,0 ms | 1951,6–4496,8 ms | 0,8647 |
| Natív NCNN | 664,7 ms | 541,9–1120,5 ms | 0,8672 |

Ez kb. háromszoros medián gyorsulás ezen a mintán, nem általános sebességígéret
vagy teljes riasztási latency. Az export és négyzetes letterbox miatt a két
backend confidence értéke nem feltétlenül bitazonos.

A teljes Pi-pipeline próbájában a megerősített motion logtól a snapshot
mentéséig kb. 1,2 másodperc telt el, majd az ntfy-küldés is sikeres volt.
A reconnect, háttértanulás, a videóban szereplő alak felismerhetősége és a
hálózat külön időt igényel. A web UI továbbra is ötmásodpercenként frissül.

[Hivatalos Ultralytics NCNN export](https://docs.ultralytics.com/integrations/ncnn/).
