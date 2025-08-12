IMAGE_NAME := dollardollar
REGISTRY := ghcr.io/myanello
VERSION := dev
IMAGE_TAG := $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
LATEST_TAG := $(REGISTRY)/$(IMAGE_NAME):latest

.PHONY: build
build:
	nerdctl build -t $(IMAGE_TAG) -t $(LATEST_TAG) . --provenance=false

.PHONY: push
push:
	nerdctl push $(LATEST_TAG)

.PHONY: build-push
build-push: build push