import unittest

import server


class TrajectoryMetadataTest(unittest.TestCase):
    def test_dataset_source_list_is_preferred_with_legacy_fallback(self):
        self.assertEqual(
            server.configured_dataset_sources({"dataset_paths": ["one", "two"]}),
            ["one", "two"],
        )
        self.assertEqual(
            server.configured_dataset_sources({"dataset_source": "legacy"}),
            ["legacy"],
        )

    def test_rollout_uses_teleop_transform(self):
        metadata = server.trajectory_metadata_for_episode(
            {"info": {}},
            {"device_type": "rollout", "collection_mode": "inference_rollout"},
        )

        self.assertEqual(metadata["transform"], "teleop_rx_minus_90")
        self.assertEqual(metadata["position_transform"], "teleop_rx_minus_90")
        self.assertEqual(metadata["quaternion_transform"], "teleop_rx_minus_90")
        self.assertEqual(metadata["world_up_axis"], "y")

    def test_unknown_device_keeps_identity_transform(self):
        metadata = server.trajectory_metadata_for_episode(
            {"info": {}},
            {"device_type": "unknown", "collection_mode": "inference_rollout"},
        )

        self.assertEqual(metadata["transform"], "identity")

    def test_iphone_umi_schema_uses_z_up_teleop_transform(self):
        info = {
            "robot_type": "umi_dual_arm_quat_3view",
            "features": {
                "head_image": {"dtype": "image", "shape": [224, 224, 3]},
                "left_wrist_image": {"dtype": "image", "shape": [224, 224, 3]},
                "right_wrist_image": {"dtype": "image", "shape": [224, 224, 3]},
                "state": {"dtype": "float32", "shape": [23]},
                "actions": {"dtype": "float32", "shape": [23]},
            },
        }

        self.assertTrue(server.iphone_umi_schema(info))
        metadata = server.trajectory_metadata_for_episode(
            {"info": {**info, "device_type": "iphone_umi1.0"}},
            {},
        )
        self.assertEqual(metadata["transform"], "teleop_rx_minus_90")
        self.assertEqual(metadata["position_transform"], "teleop_rx_minus_90")
        self.assertEqual(metadata["quaternion_transform"], "identity")
        self.assertEqual(metadata["source_world_up_axis"], "z")
        self.assertEqual(metadata["world_up_axis"], "y")
        self.assertEqual(metadata["state_layout"], "left8_right8_head7")
        self.assertEqual(metadata["quaternion_order"], "wxyz")

        self.assertEqual(server.dataset_device_type(info), "iphone_umi1.0")

        invalid = {**info, "features": {**info["features"], "state": {"dtype": "float32", "shape": [22]}}}
        self.assertFalse(server.iphone_umi_schema(invalid))

    def test_iphone_umi_embedded_images_map_to_existing_camera_contract(self):
        videos = server.embedded_videos_for_episodes(
            [{"episode_index": 7}],
            {7: "data/chunk-000/episode_000007.parquet"},
        )[7]

        self.assertEqual(
            [video["camera"] for video in videos],
            ["image", "wrist_image_1", "wrist_image_2"],
        )
        self.assertTrue(all(video["kind"] == "parquet_image" for video in videos))
        self.assertTrue(all(video["data_rel_path"].endswith("episode_000007.parquet") for video in videos))


if __name__ == "__main__":
    unittest.main()
