def test_kernel_importable() -> None:
    import hearth

    assert hearth is not None


def test_primitives_subpackage_importable() -> None:
    from hearth import primitives

    assert primitives is not None
